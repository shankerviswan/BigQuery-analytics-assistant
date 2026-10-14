"""Offline golden / must_reject cases (no BigQuery required)."""
from pathlib import Path
import yaml

from app.sql_validator import validate_sql
from app.metric_check import check_metric_alignment
from app.assistant import load_yaml
from app.date_context import DateContext
from datetime import date

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
