from app.assistant import AnalyticsAssistant
from app.llm import SQLProposal
from app.date_context import DateContext
from datetime import date


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
    live_schema = False  # force static schema in unit tests


class FakeProvider:
    def __init__(self, proposal):
        self.proposal = proposal

    def propose_sql(self, question, schema_context, metrics_context, date_context=""):
        return self.proposal


class FakeExecutor:
    def __init__(self, result):
        self.result = result
        self.called = False
        self.client = None  # schema path not used when live_schema=False

    def execute(self, sql, parameters, allowed_tables):
        self.called = True
        return self.result


def _date_ctx():
    return DateContext(timezone="UTC", today=date(2026, 10, 14), now_iso="2026-10-14T12:00:00+00:00")


def test_ambiguous_sales_asks_before_execution():
    executor = FakeExecutor({})
    provider = FakeProvider(SQLProposal(clarification="Which sales definition do you mean?"))
    assistant = AnalyticsAssistant(
        FakeSettings(), provider=provider, executor=executor, date_ctx=_date_ctx()
    )
    result = assistant.ask("What were sales?")
    assert result["status"] == "clarification_required"
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
            "SELECT COUNT(DISTINCT o.id) AS order_count "
            "FROM `bigquery-public-data.thelook_ecommerce.orders` AS o"
        ),
        metric_id="order_count",
    ))
    assistant = AnalyticsAssistant(
        FakeSettings(), provider=provider, executor=executor, date_ctx=_date_ctx()
    )
    result = assistant.ask("How many orders?")
    assert result["answer"] == "order_count: 7"
    assert result["status"] == "succeeded"
    assert "date_context" in result


def test_query_failure_is_not_replaced_with_a_number():
    provider = FakeProvider(SQLProposal(
        sql=(
            "SELECT COUNT(DISTINCT o.id) AS order_count "
            "FROM `bigquery-public-data.thelook_ecommerce.orders` AS o"
        ),
        metric_id="order_count",
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
    from app.metric_check import check_metric_alignment

    metric = {
        "tables": ["bigquery-public-data.thelook_ecommerce.orders"],
        "sql_rule": "COUNT(DISTINCT o.id) WHERE LOWER(o.status) = 'cancelled'",
    }
    bad_sql = "SELECT COUNT(*) FROM `bigquery-public-data.thelook_ecommerce.products`"
    result = check_metric_alignment(bad_sql, metric)
    assert not result.ok
    assert "missing required table" in result.reason.lower()


def test_metric_alignment_requires_cancelled_filter():
    from app.metric_check import check_metric_alignment

    metric = {
        "tables": ["bigquery-public-data.thelook_ecommerce.orders"],
        "sql_rule": "COUNT(DISTINCT o.id) WHERE LOWER(o.status) = 'cancelled'",
    }
    sql = (
        "SELECT COUNT(DISTINCT o.id) AS cancelled_order_count "
        "FROM `bigquery-public-data.thelook_ecommerce.orders` AS o"
    )
    result = check_metric_alignment(sql, metric)
    assert not result.ok
