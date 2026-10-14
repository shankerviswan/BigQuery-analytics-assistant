"""Offline golden / must_reject cases (no BigQuery required)."""
from pathlib import Path
from datetime import date
import yaml

from app.sql_validator import validate_sql
from app.metric_check import check_metric_alignment
from app.assistant import AnalyticsAssistant, load_yaml
from app.date_context import DateContext
from app.llm import DemoProvider

ROOT = Path(__file__).resolve().parents[2]
CASES = yaml.safe_load((Path(__file__).parent / "cases.yaml").read_text())["cases"]

ALLOWED = set(load_yaml(ROOT / "config/allowed_tables.yaml")["allowed_tables"])
METRICS = load_yaml(ROOT / "config/metrics.yaml")["metrics"]


def test_policy_must_reject_sql_cases():
    for case in CASES:
        sql = case.get("must_reject_sql")
        if not sql:
            continue
        if case.get("expect_metric"):
            metric = METRICS[case["expect_metric"]]
            result = check_metric_alignment(sql, metric)
            assert not result.ok, f"{case['id']} should fail metric alignment: {result.reason}"
        else:
            result = validate_sql(sql, ALLOWED)
            assert not result.ok, f"{case['id']} should fail SQL policy: {result.reason}"


class _Settings:
    project_id = "unused"
    location = "US"
    max_bytes_billed = 100_000_000
    timeout_seconds = 30
    max_result_rows = 100
    business_timezone = "America/Los_Angeles"
    llm_base_url = None
    llm_api_key = None
    llm_model = None
    llm_disable_thinking = False
    live_schema = False


class _Executor:
    def __init__(self, result):
        self.result = result
        self.client = None
        self.last_params = None

    def execute(self, sql, parameters, allowed_tables):
        self.last_params = parameters
        return self.result


def test_january_2024_replays_recorded_count_without_bigquery():
    case = next(item for item in CASES if item["id"] == "january_2024_not_2025")
    ctx = DateContext(
        timezone=case["timezone"],
        today=date.fromisoformat(case["as_of"]),
        now_iso="2026-10-14T12:00:00-07:00",
    )
    executor = _Executor({
        "status": "succeeded",
        "job_id": case["live_job_id"],
        "sql": "",
        "parameters": {},
        "source_tables": ["bigquery-public-data.thelook_ecommerce.orders"],
        "estimated_bytes_processed": case["live_bytes_processed"],
        "bytes_processed": case["live_bytes_processed"],
        "bytes_billed": case["live_bytes_billed"],
        "cache_hit": False,
        "rows": [{"order_count": case["expect_order_count"]}],
        "truncated": False,
    })
    assistant = AnalyticsAssistant(
        _Settings(),
        provider=DemoProvider(date_ctx=ctx),
        executor=executor,
        date_ctx=ctx,
    )
    result = assistant.ask(case["question"])
    assert result["status"] == "succeeded"
    assert result["answer"] == f"order_count: {case['expect_order_count']}"
    assert result["metric_id"] == "order_count"
    assert result["date_context"]["time_range"] == case["expect_time_range"]
    assert executor.last_params["start_date"] == case["expect_start"]
    assert executor.last_params["end_date"] == case["expect_end"]


def test_january_2024_cancelled_replays_recorded_count_without_bigquery():
    case = next(item for item in CASES if item["id"] == "january_2024_cancelled")
    ctx = DateContext(
        timezone=case["timezone"],
        today=date.fromisoformat(case["as_of"]),
        now_iso="2026-10-14T12:00:00-07:00",
    )
    executor = _Executor({
        "status": "succeeded",
        "job_id": case["live_job_id"],
        "sql": "",
        "parameters": {},
        "source_tables": ["bigquery-public-data.thelook_ecommerce.orders"],
        "estimated_bytes_processed": case["live_bytes_processed"],
        "bytes_processed": case["live_bytes_processed"],
        "bytes_billed": case["live_bytes_billed"],
        "cache_hit": False,
        "rows": [{case["expect_metric"]: case["expect_order_count"]}],
        "truncated": False,
    })
    assistant = AnalyticsAssistant(
        _Settings(),
        provider=DemoProvider(date_ctx=ctx),
        executor=executor,
        date_ctx=ctx,
    )
    result = assistant.ask(case["question"])
    assert result["status"] == "succeeded"
    assert result["metric_id"] == case["expect_metric"]
    assert result["answer"] == f"{case['expect_metric']}: {case['expect_order_count']}"
    assert result["date_context"]["time_range"] == case["expect_time_range"]
    assert executor.last_params["start_date"] == case["expect_start"]
    assert executor.last_params["end_date"] == case["expect_end"]


def test_january_year_not_hardcoded():
    from app.llm import DemoProvider, _extract_month_kind
    assert _extract_month_kind("orders in january 2024") == "january_2024"
    assert _extract_month_kind("January 2023 cancellations") == "january_2023"
    assert _extract_month_kind("last month") is None

    ctx = DateContext(timezone="UTC", today=date(2026, 10, 14), now_iso="2026-10-14T12:00:00+00:00")
    provider = DemoProvider(date_ctx=ctx)
    proposal = provider.propose_sql(
        "How many orders were created in January 2024?",
        schema_context="",
        metrics_context="",
    )
    assert proposal.time_range == "january_2024"
