"""Metric-to-SQL alignment using structured metric fields + sqlglot AST."""
from __future__ import annotations

from dataclasses import dataclass
import re

import sqlglot
from sqlglot import exp


@dataclass
class MetricCheckResult:
    ok: bool
    reason: str = ""


def _table_names(tree: exp.Expression) -> set[str]:
    names: set[str] = set()
    for t in tree.find_all(exp.Table):
        names.add(".".join(p.name for p in t.parts).lower())
        names.add(t.name.lower())
    return names


def _column_name(node: exp.Expression | None) -> str | None:
    if node is None:
        return None
    if isinstance(node, exp.Column):
        return node.name.lower()
    cols = list(node.find_all(exp.Column)) if hasattr(node, "find_all") else []
    if cols:
        return cols[0].name.lower()
    return None


def _literal_values(node: exp.Expression | None) -> set[str]:
    vals: set[str] = set()
    if node is None:
        return vals
    if isinstance(node, exp.Literal):
        vals.add(str(node.this).strip("'\"").lower())
        return vals
    for lit in node.find_all(exp.Literal):
        vals.add(str(lit.this).strip("'\"").lower())
    return vals


def _and_conjuncts(node: exp.Expression | None) -> list[exp.Expression]:
    if node is None:
        return []
    if isinstance(node, exp.And):
        return _and_conjuncts(node.left) + _and_conjuncts(node.right)
    return [node]


def _where_clause(tree: exp.Expression) -> exp.Expression | None:
    for sel in tree.find_all(exp.Select):
        where = sel.args.get("where")
        if where is not None:
            return where.this if isinstance(where, exp.Where) else where
    return None


def _has_agg_on_column(tree: exp.Expression, agg: str, column: str) -> bool:
    col = column.lower()
    agg = agg.lower()

    if agg == "count_distinct":
        for count in tree.find_all(exp.Count):
            piece = count.sql(dialect="bigquery").lower().replace(" ", "")
            if "distinct" in piece and col in piece:
                return True
        return False

    if agg == "sum":
        for s in tree.find_all(exp.Sum):
            piece = s.sql(dialect="bigquery").lower()
            if col in piece:
                return True
        return False

    if agg == "count":
        return any(True for _ in tree.find_all(exp.Count))

    return True


def _filter_ok_ast(tree: exp.Expression, f: dict) -> bool:
    col = (f.get("column") or "").lower()
    op = (f.get("op") or "eq").lower()
    val = f.get("value")
    if not col:
        return True

    where = _where_clause(tree)
    if where is None:
        return False

    for conj in _and_conjuncts(where):
        if op == "eq":
            target = str(val).lower()
            if isinstance(conj, exp.EQ):
                left_col = _column_name(conj.left)
                right_vals = _literal_values(conj.right)
                if left_col == col and target in right_vals:
                    return True
            if isinstance(conj, exp.In):
                left_col = _column_name(conj.this)
                if left_col == col and target in _literal_values(conj):
                    return True

        if op == "not_in":
            required = {str(x).lower() for x in (val or [])}
            if isinstance(conj, exp.Not) and isinstance(conj.this, exp.In):
                inn = conj.this
                left_col = _column_name(inn.this)
                if left_col == col and required & _literal_values(inn):
                    return True
            if isinstance(conj, exp.Not):
                inner = conj.this
                if isinstance(inner, exp.In):
                    left_col = _column_name(inner.this)
                    if left_col == col and required & _literal_values(inner):
                        return True

    return False


def _time_bounds_ok(tree: exp.Expression, time_column: str) -> bool:
    col = time_column.lower()
    where = _where_clause(tree)
    if where is None:
        return False

    sql_lower = tree.sql(dialect="bigquery").lower()
    if "@start_date" not in sql_lower or "@end_date" not in sql_lower:
        return False

    if re.search(rf"{col}\s*(>=|>|<|<=|=)\s*'?\d{{4}}-\d{{2}}-\d{{2}}", sql_lower):
        return False

    has_ge_start = False
    has_lt_end = False
    for conj in _and_conjuncts(where):
        if isinstance(conj, (exp.GTE, exp.GT)):
            left_col = _column_name(conj.left)
            right_sql = conj.right.sql(dialect="bigquery").lower() if conj.right else ""
            if left_col == col and "start_date" in right_sql:
                has_ge_start = True
        if isinstance(conj, (exp.LT, exp.LTE)):
            left_col = _column_name(conj.left)
            right_sql = conj.right.sql(dialect="bigquery").lower() if conj.right else ""
            if left_col == col and "end_date" in right_sql:
                has_lt_end = True

    if has_ge_start and has_lt_end:
        return True
    return col in sql_lower and "@start_date" in sql_lower and "@end_date" in sql_lower


def _group_by_ok(tree: exp.Expression, group_by: list[str] | None) -> bool:
    if not group_by:
        return True
    required = {g.lower() for g in group_by}
    found: set[str] = set()
    for gb in tree.find_all(exp.Group):
        for e in gb.expressions:
            name = _column_name(e)
            if name:
                found.add(name)
            else:
                found.add(e.sql(dialect="bigquery").lower())
    return required.issubset(found)


def check_metric_alignment(sql: str, metric: dict | None) -> MetricCheckResult:
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
    missing = []
    for t in required:
        short = t.split(".")[-1]
        if t not in present and short not in present:
            missing.append(t)
    if missing:
        return MetricCheckResult(
            False,
            "SQL is missing required table(s) for metric: " + ", ".join(sorted(missing)),
        )

    column = metric.get("column")
    agg = metric.get("agg")
    if column and agg and not _has_agg_on_column(tree, agg, column):
        return MetricCheckResult(
            False,
            f"Metric expects {agg}({column}), but SQL does not clearly express that aggregate.",
        )

    time_col = metric.get("time_column")
    if time_col and not _time_bounds_ok(tree, time_col):
        return MetricCheckResult(
            False,
            f"Metric time_column '{time_col}' must use half-open bounds "
            "against @start_date and @end_date (no hardcoded date literals).",
        )

    for f in metric.get("filters") or []:
        if not _filter_ok_ast(tree, f):
            return MetricCheckResult(
                False,
                f"Metric requires filter on {f.get('column')} ({f.get('op')} {f.get('value')}), "
                "but SQL WHERE clause does not satisfy it.",
            )

    group_by = metric.get("group_by")
    if group_by and not _group_by_ok(tree, group_by):
        return MetricCheckResult(
            False,
            f"Metric requires GROUP BY {group_by}, but SQL does not include those columns.",
        )

    return MetricCheckResult(True, "Metric alignment checks passed.")
