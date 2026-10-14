"""Metric-to-SQL alignment using structured metric fields + sqlglot AST."""
from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp


@dataclass
class MetricCheckResult:
    ok: bool
    reason: str = ""


def _table_names(tree: exp.Expression) -> set[str]:
    return {
        ".".join(p.name for p in t.parts).lower()
        for t in tree.find_all(exp.Table)
    }


def _sql_mentions_column(tree: exp.Expression, column: str) -> bool:
    col = column.lower()
    for c in tree.find_all(exp.Column):
        if c.name.lower() == col:
            return True
    return col in tree.sql(dialect="bigquery").lower()


def _has_agg(tree: exp.Expression, agg: str, column: str) -> bool:
    """Rough check that the intended aggregate appears."""
    sql = tree.sql(dialect="bigquery").lower().replace(" ", "")
    col = column.lower()
    if agg == "count_distinct":
        return f"count(distinct" in sql and col in sql
    if agg == "sum":
        return "sum(" in sql and col in sql
    if agg == "count":
        return "count(" in sql
    return True


def _filter_satisfied(sql_lower: str, f: dict) -> bool:
    """Check required filter polarity appears in SQL text (pragmatic PoC).

    Values must appear as quoted string literals so an alias like
    cancelled_order_count does not satisfy status = cancelled.
    """
    col = (f.get("column") or "").lower()
    op = (f.get("op") or "eq").lower()
    val = f.get("value")
    if not col:
        return True
    if col not in sql_lower:
        return False

    def _literal_present(v: str) -> bool:
        v = v.lower()
        return (
            f"'{v}'" in sql_lower
            or f'"{v}"' in sql_lower
            or f"'{v.upper()}'" in sql_lower
            or f"'{v.title()}'" in sql_lower
        )

    if op == "eq":
        v = str(val).lower()
        return _literal_present(v) and ("=" in sql_lower or " like " in sql_lower)
    if op == "not_in":
        values = [str(x).lower() for x in (val or [])]
        has_neg = "not in" in sql_lower or "<>" in sql_lower or "!=" in sql_lower or " not " in sql_lower
        has_val = any(_literal_present(v) for v in values)
        return has_neg and has_val
    return True


def check_metric_alignment(sql: str, metric: dict | None) -> MetricCheckResult:
    """Validate SQL against structured metric definition.

    Requires a metric. Callers should refuse ad-hoc SQL when metric_id is missing.
    """
    if not metric:
        return MetricCheckResult(
            False,
            "No approved metric selected. Choose an approved metric or clarify the question.",
        )

    try:
        tree = sqlglot.parse_one(sql, read="bigquery")
    except Exception as exc:
        return MetricCheckResult(False, f"Cannot parse SQL for metric check: {exc}")

    required = {t.lower() for t in (metric.get("tables") or [])}
    present = _table_names(tree)
    missing = required - present
    if missing:
        return MetricCheckResult(
            False,
            "SQL is missing required table(s) for metric: " + ", ".join(sorted(missing)),
        )

    column = metric.get("column")
    agg = metric.get("agg")
    if column and agg:
        if not _has_agg(tree, agg, column):
            return MetricCheckResult(
                False,
                f"Metric expects {agg}({column}), but SQL does not clearly express that aggregate.",
            )

    time_col = metric.get("time_column")
    if time_col and not _sql_mentions_column(tree, time_col):
        return MetricCheckResult(
            False,
            f"Metric time_column '{time_col}' is not referenced in SQL (date filter likely missing).",
        )

    for f in metric.get("filters") or []:
        sql_lower = sql.lower()
        if not _filter_satisfied(sql_lower, f):
            return MetricCheckResult(
                False,
                f"Metric requires filter on {f.get('column')} ({f.get('op')} {f.get('value')}), "
                "but SQL does not satisfy it.",
            )

    return MetricCheckResult(True, "Metric alignment checks passed.")
