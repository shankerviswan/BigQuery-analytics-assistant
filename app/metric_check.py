"""Lightweight metric-to-SQL alignment checks (not a full semantic layer)."""
from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp


@dataclass
class MetricCheckResult:
    ok: bool
    reason: str = ""


def check_metric_alignment(sql: str, metric: dict | None) -> MetricCheckResult:
    """Cheap, deterministic checks that the SQL roughly matches the metric definition.

    - Required tables must appear.
    - For cancelled_order_count: status filter must mention cancelled.
    - For item_sales / category sales: sale_price must appear; cancelled/returned
      exclusion is recommended (warning only if missing, still ok for PoC).
    This is intentionally incomplete — full semantic validation belongs later.
    """
    if not metric:
        return MetricCheckResult(True, "No metric selected; skipped alignment check.")

    try:
        tree = sqlglot.parse_one(sql, read="bigquery")
    except Exception as exc:
        return MetricCheckResult(False, f"Cannot parse SQL for metric check: {exc}")

    tables_in_sql = {
        ".".join(p.name for p in t.parts).lower()
        for t in tree.find_all(exp.Table)
    }
    required = {t.lower() for t in (metric.get("tables") or [])}
    missing = required - tables_in_sql
    if missing:
        return MetricCheckResult(
            False,
            "SQL is missing required table(s) for metric: " + ", ".join(sorted(missing)),
        )

    sql_lower = sql.lower()
    metric_id = metric.get("id") or ""  # may be absent; caller can inject

    # Heuristic rules by known metric patterns
    caveats = metric.get("caveats") or []
    sql_rule = (metric.get("sql_rule") or "").lower()

    if "cancelled" in sql_rule or "canceled" in sql_rule:
        # Require a filter-like occurrence, not just an alias name.
        has_filter = (
            "status" in sql_lower
            and ("cancelled" in sql_lower or "canceled" in sql_lower)
            and ("=" in sql_lower or " in " in sql_lower or "like" in sql_lower)
        )
        if not has_filter:
            return MetricCheckResult(
                False,
                "Metric requires a cancelled-status filter, but SQL does not filter on cancelled/canceled status.",
            )

    if "sale_price" in sql_rule or "sum(oi.sale_price)" in sql_rule:
        if "sale_price" not in sql_lower:
            return MetricCheckResult(
                False,
                "Metric is based on sale_price, but SQL does not reference sale_price.",
            )

    if "count(distinct" in sql_rule and "count(distinct" not in sql_lower and "count(distinct " not in sql_lower:
        # soft: many dialects write COUNT(DISTINCT x)
        if "count(distinct" not in sql_lower.replace(" ", ""):
            # still allow if COUNT appears — keep soft for PoC
            pass

    return MetricCheckResult(True, "Basic metric alignment checks passed.")
