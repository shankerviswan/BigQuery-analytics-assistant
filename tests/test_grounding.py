from datetime import date

from app.assistant import AnalyticsAssistant
from app.llm import SQLProposal
from app.date_context import DateContext
from app.metric_check import check_metric_alignment


class FakeSettings:
    project_id = "unused"
    location = "US"
    max_bytes_billed = 1000
    timeout_seconds = 10
    max_result_rows = 10
    business_timezone = "UTC"
    llm_base_url = None
    llm_api_key = None
    llm_model = None
    live_schema = False


class FakeProvider:
    def __init__(self, proposal):
        self.proposal = proposal

    def propose_sql(self, question, schema_context, metrics_context, date_context=""):
        return self.proposal


class FakeExecutor:
    def __init__(self, result):
        self.result = result
        self.called = False
        self.client = None

    def execute(self, sql, parameters, allowed_tables):
        self.called = True
        self.last_params = parameters
        return self.result


def _date_ctx():
    return DateContext(
        timezone="UTC",
        today=date(2026, 10, 14),
        now_iso="2026-10-14T12:00:00+00:00",
    )


def test_ambiguous_sales_asks_before_execution():
    executor = FakeExecutor({})
    provider = FakeProvider(SQLProposal(clarification="Which sales definition do you mean?"))
    assistant = AnalyticsAssistant(
        FakeSettings(), provider=provider, executor=executor, date_ctx=_date_ctx()
    )
    result = assistant.ask("What were sales?")
    assert result["status"] == "clarification_required"
    assert not executor.called


def test_missing_metric_id_refused():
    executor = FakeExecutor({})
    provider = FakeProvider(SQLProposal(
        sql="SELECT COUNT(*) FROM `bigquery-public-data.thelook_ecommerce.orders`",
        metric_id=None,
    ))
    assistant = AnalyticsAssistant(
        FakeSettings(), provider=provider, executor=executor, date_ctx=_date_ctx()
    )
    result = assistant.ask("How many orders?")
    assert result["status"] == "refused"
    assert "metric_id" in result["reason"].lower()
    assert not executor.called


def test_answer_uses_actual_result_value():
    executor = FakeExecutor({
        "status": "succeeded",
        "job_id": "test-job",
        "sql": "SELECT 7 AS order_count",
        "parameters": {},
        "source_tables": ["bigquery-public-data.thelook_ecommerce.orders"],
        "estimated_bytes_processed": 10,
        "bytes_processed": 10,
        "bytes_billed": 10,
        "cache_hit": False,
        "rows": [{"order_count": 7}],
        "truncated": False,
    })
    provider = FakeProvider(SQLProposal(
        sql=(
            "SELECT COUNT(DISTINCT o.order_id) AS order_count "
            "FROM `bigquery-public-data.thelook_ecommerce.orders` AS o "
            "WHERE o.created_at >= @start_date AND o.created_at < @end_date"
        ),
        metric_id="order_count",
        time_range="last_month",
    ))
    assistant = AnalyticsAssistant(
        FakeSettings(), provider=provider, executor=executor, date_ctx=_date_ctx()
    )
    result = assistant.ask("How many orders?")
    assert result["answer"] == "order_count: 7"
    assert result["status"] == "succeeded"
    assert result["schema_source"] == "static"
    assert "start_date" in executor.last_params
    assert executor.last_params["start_date"] == "2026-09-01T00:00:00+00:00"
    assert executor.last_params["end_date"] == "2026-10-01T00:00:00+00:00"


def test_truncated_single_cell_is_not_printed_as_the_answer():
    executor = FakeExecutor({
        "status": "succeeded",
        "job_id": "test-job",
        "sql": "SELECT 7 AS order_count",
        "parameters": {},
        "source_tables": ["bigquery-public-data.thelook_ecommerce.orders"],
        "estimated_bytes_processed": 10,
        "bytes_processed": 10,
        "bytes_billed": 10,
        "cache_hit": False,
        "rows": [{"order_count": 7}],
        "truncated": True,
    })
    provider = FakeProvider(SQLProposal(
        sql=(
            "SELECT COUNT(DISTINCT o.order_id) AS order_count "
            "FROM `bigquery-public-data.thelook_ecommerce.orders` AS o "
            "WHERE o.created_at >= @start_date AND o.created_at < @end_date"
        ),
        metric_id="order_count",
        time_range="last_month",
    ))
    assistant = AnalyticsAssistant(
        FakeSettings(), provider=provider, executor=executor, date_ctx=_date_ctx()
    )
    result = assistant.ask("How many orders?")
    assert "order_count: 7" not in result["answer"]
    assert "row cap" in result["answer"]
    assert "not the full count" in result["answer"]


def test_truncated_multi_row_says_the_length_is_not_the_full_count():
    executor = FakeExecutor({
        "status": "succeeded",
        "job_id": "test-job",
        "sql": "SELECT 1",
        "parameters": {},
        "source_tables": ["bigquery-public-data.thelook_ecommerce.order_items"],
        "estimated_bytes_processed": 10,
        "bytes_processed": 10,
        "bytes_billed": 10,
        "cache_hit": False,
        "rows": [{"category": "a", "category_item_sales": 1}, {"category": "b", "category_item_sales": 2}],
        "truncated": True,
    })
    provider = FakeProvider(SQLProposal(
        sql=(
            "SELECT p.category, SUM(oi.sale_price) AS category_item_sales "
            "FROM `bigquery-public-data.thelook_ecommerce.order_items` AS oi "
            "JOIN `bigquery-public-data.thelook_ecommerce.products` AS p "
            "ON oi.product_id = p.id "
            "WHERE LOWER(oi.status) NOT IN ('cancelled', 'returned') "
            "AND oi.created_at >= @start_date AND oi.created_at < @end_date "
            "GROUP BY p.category"
        ),
        metric_id="category_item_sales",
        time_range="last_month",
    ))
    assistant = AnalyticsAssistant(
        FakeSettings(), provider=provider, executor=executor, date_ctx=_date_ctx()
    )
    result = assistant.ask("Sales by category last month?")
    assert "2 row(s)" in result["answer"]
    assert "row cap" in result["answer"]
    assert "not the full count" in result["answer"]


def test_query_failure_is_not_replaced_with_a_number():
    provider = FakeProvider(SQLProposal(
        sql=(
            "SELECT COUNT(DISTINCT o.order_id) AS order_count "
            "FROM `bigquery-public-data.thelook_ecommerce.orders` AS o "
            "WHERE o.created_at >= @start_date AND o.created_at < @end_date"
        ),
        metric_id="order_count",
        time_range="last_month",
    ))

    class BrokenExecutor:
        client = None

        def execute(self, *args, **kwargs):
            raise RuntimeError("query failed")

    assistant = AnalyticsAssistant(
        FakeSettings(), provider=provider, executor=BrokenExecutor(), date_ctx=_date_ctx()
    )
    try:
        assistant.ask("How many orders?")
        assert False, "Expected exception"
    except RuntimeError as exc:
        assert "query failed" in str(exc)


def test_metric_alignment_rejects_wrong_tables():
    metric = {
        "tables": ["bigquery-public-data.thelook_ecommerce.orders"],
        "agg": "count_distinct",
        "column": "order_id",
        "time_column": "created_at",
        "filters": [],
    }
    bad_sql = "SELECT COUNT(*) FROM `bigquery-public-data.thelook_ecommerce.products`"
    result = check_metric_alignment(bad_sql, metric)
    assert not result.ok


def test_metric_alignment_requires_cancelled_filter():
    metric = {
        "tables": ["bigquery-public-data.thelook_ecommerce.orders"],
        "agg": "count_distinct",
        "column": "order_id",
        "time_column": "created_at",
        "filters": [
            {"column": "status", "op": "eq", "value": "cancelled", "case_insensitive": True}
        ],
    }
    sql = (
        "SELECT COUNT(DISTINCT o.order_id) AS cancelled_order_count "
        "FROM `bigquery-public-data.thelook_ecommerce.orders` AS o "
        "WHERE LOWER(o.status) = 'complete' AND o.created_at >= @start_date"
    )
    result = check_metric_alignment(sql, metric)
    assert not result.ok


def test_metric_alignment_rejects_missing_time_column():
    metric = {
        "tables": ["bigquery-public-data.thelook_ecommerce.orders"],
        "agg": "count_distinct",
        "column": "order_id",
        "time_column": "created_at",
        "filters": [],
    }
    sql = (
        "SELECT COUNT(DISTINCT o.order_id) AS order_count "
        "FROM `bigquery-public-data.thelook_ecommerce.orders` AS o"
    )
    result = check_metric_alignment(sql, metric)
    assert not result.ok
    assert "time_column" in result.reason.lower() or "created_at" in result.reason.lower()


def test_null_metric_refused_by_checker():
    result = check_metric_alignment("SELECT 1", None)
    assert not result.ok
