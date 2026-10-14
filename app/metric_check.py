"""Metric-to-SQL alignment using structured metric fields + sqlglot scopes."""
from __future__ import annotations

from dataclasses import dataclass
import re

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import Scope, ScopeType, traverse_scope

from app.sql_validator import extract_tables


@dataclass
class MetricCheckResult:
    ok: bool
    reason: str = ""


def _unwrap(node: exp.Expression | None) -> exp.Expression | None:
    while isinstance(node, exp.Paren):
        node = node.this
    return node


def _and_conjuncts(node: exp.Expression | None) -> list[exp.Expression]:
    if node is None:
        return []
    node = _unwrap(node)
    if isinstance(node, exp.And):
        return _and_conjuncts(node.left) + _and_conjuncts(node.right)
    return [node]


def _output_scopes(tree: exp.Expression) -> list[Scope]:
    """Scopes whose rows are the query result. UNION branches, otherwise the root SELECT."""
    scopes = list(traverse_scope(tree))
    unions = [
        scope
        for scope in scopes
        if scope.scope_type == ScopeType.UNION and isinstance(scope.expression, exp.Select)
    ]
    if unions:
        return unions
    return [
        scope
        for scope in scopes
        if scope.scope_type == ScopeType.ROOT and isinstance(scope.expression, exp.Select)
    ]


def _selected_aliases(scope: Scope) -> list[str]:
    expr = scope.expression
    nodes: list[exp.Expression] = []
    from_ = expr.args.get("from_") or expr.args.get("from")
    if from_ is not None:
        nodes.append(from_)
    nodes.extend(expr.args.get("joins") or [])
    aliases: list[str] = []
    for node in nodes:
        for table in node.find_all(exp.Table):
            aliases.append(table.alias_or_name)
    return aliases


def _governing_scopes(scope: Scope) -> list[Scope]:
    """This SELECT plus CTE scopes it actually reads. Unused CTEs are ignored."""
    found: list[Scope] = []
    seen: set[int] = set()

    def walk(current: Scope) -> None:
        if id(current) in seen:
            return
        seen.add(id(current))
        found.append(current)
        for alias in _selected_aliases(current):
            source = current.sources.get(alias)
            if isinstance(source, Scope):
                walk(source)

    walk(scope)
    return found


def _wheres(scopes: list[Scope]) -> list[exp.Expression]:
    nodes: list[exp.Expression] = []
    for scope in scopes:
        where = scope.expression.args.get("where")
        if where is None:
            continue
        nodes.append(where.this if isinstance(where, exp.Where) else where)
    return nodes


def _conjuncts(scopes: list[Scope]) -> list[exp.Expression]:
    conjuncts: list[exp.Expression] = []
    for where in _wheres(scopes):
        conjuncts.extend(_and_conjuncts(where))
    return conjuncts


def _compared_column(node: exp.Expression | None, case_insensitive: bool) -> str | None:
    """Column a predicate compares. Case-insensitive metrics must use LOWER(column)."""
    node = _unwrap(node)
    if node is None:
        return None
    if isinstance(node, exp.Lower):
        inner = _unwrap(node.this)
        if isinstance(inner, exp.Column):
            return inner.name.lower()
        return None
    if case_insensitive:
        return None
    if isinstance(node, exp.Column):
        return node.name.lower()
    return None


def _literal_values(node: exp.Expression | None) -> set[str]:
    vals: set[str] = set()
    if node is None:
        return vals
    node = _unwrap(node)
    if isinstance(node, exp.Literal):
        vals.add(str(node.this).strip("'\"").lower())
        return vals
    for lit in node.find_all(exp.Literal):
        vals.add(str(lit.this).strip("'\"").lower())
    return vals


def _has_param(node: exp.Expression | None, name: str) -> bool:
    if node is None:
        return False
    for param in node.find_all(exp.Parameter):
        if str(param.name).lower() == name:
            return True
    return False


def _exact_agg_column(node: exp.Expression | None, column: str) -> bool:
    node = _unwrap(node)
    if isinstance(node, exp.Distinct):
        exprs = list(node.expressions or [])
        if len(exprs) != 1:
            return False
        node = _unwrap(exprs[0])
    if isinstance(node, exp.Column):
        return node.name.lower() == column
    return False


def _has_agg_on_column(scopes: list[Scope], agg: str, column: str) -> bool:
    col = column.lower()
    agg = agg.lower()
    counts: list[exp.Count] = []
    sums: list[exp.Sum] = []
    for scope in scopes:
        for expression in scope.expression.expressions or []:
            counts.extend(expression.find_all(exp.Count))
            sums.extend(expression.find_all(exp.Sum))

    if agg == "count_distinct":
        return any(_exact_agg_column(count.this, col) for count in counts)
    if agg == "sum":
        return any(_exact_agg_column(item.this, col) for item in sums)
    if agg == "count":
        return any(
            _exact_agg_column(count.this, col)
            for count in counts
            if not isinstance(count.this, exp.Distinct)
        )
    return False


def _filter_ok(scopes: list[Scope], filt: dict) -> bool:
    col = (filt.get("column") or "").lower()
    op = (filt.get("op") or "eq").lower()
    value = filt.get("value")
    case_insensitive = bool(filt.get("case_insensitive"))
    if not col:
        return True

    conjuncts = _conjuncts(scopes)
    if op == "eq":
        target = str(value).lower()
        for conj in conjuncts:
            conj = _unwrap(conj)
            if isinstance(conj, exp.EQ) and _compared_column(conj.left, case_insensitive) == col:
                if target in _literal_values(conj.right):
                    return True
        return False

    if op == "not_in":
        required = {str(item).lower() for item in (value or [])}
        excluded: set[str] = set()
        for conj in conjuncts:
            conj = _unwrap(conj)
            if isinstance(conj, exp.Not) and isinstance(_unwrap(conj.this), exp.In):
                inner = _unwrap(conj.this)
                if _compared_column(inner.this, case_insensitive) == col:
                    excluded |= _literal_values(inner)
            elif isinstance(conj, exp.NEQ) and _compared_column(conj.left, case_insensitive) == col:
                excluded |= _literal_values(conj.right)
        return required <= excluded

    return False


def _time_bounds_ok(scopes: list[Scope], time_column: str) -> bool:
    col = time_column.lower()
    has_ge_start = False
    has_lt_end = False
    for conj in _conjuncts(scopes):
        conj = _unwrap(conj)
        if not isinstance(conj, (exp.GTE, exp.GT, exp.LT, exp.LTE, exp.EQ)):
            continue
        if _column_name(conj.left) != col:
            continue
        if _has_date_literal(conj.right):
            return False
        if isinstance(conj, exp.GTE) and _has_param(conj.right, "start_date"):
            has_ge_start = True
        if isinstance(conj, exp.LT) and _has_param(conj.right, "end_date"):
            has_lt_end = True
    return has_ge_start and has_lt_end


def _column_name(node: exp.Expression | None) -> str | None:
    node = _unwrap(node)
    if isinstance(node, exp.Column):
        return node.name.lower()
    return None


def _has_date_literal(node: exp.Expression | None) -> bool:
    if node is None:
        return False
    for lit in node.find_all(exp.Literal):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(lit.this)):
            return True
    return False


def _group_by_ok(scopes: list[Scope], group_by: list[str] | None) -> bool:
    if not group_by:
        return True
    required = {name.lower() for name in group_by}
    found: set[str] = set()
    for scope in scopes:
        group = scope.expression.args.get("group")
        if group is None:
            continue
        for expression in group.expressions:
            name = _column_name(expression)
            if name:
                found.add(name)
    return required <= found


def _joins_ok(scopes: list[Scope], tables: list[str]) -> bool:
    if len(tables) < 2:
        return True
    saw_equijoin = False
    for scope in scopes:
        for join in scope.expression.args.get("joins") or []:
            kind = str(join.args.get("kind") or "").upper()
            on = join.args.get("on")
            if kind == "CROSS" or on is None:
                return False
            for eq in on.find_all(exp.EQ):
                if isinstance(_unwrap(eq.left), exp.Column) and isinstance(_unwrap(eq.right), exp.Column):
                    saw_equijoin = True
    return saw_equijoin


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

    required = [t.lower() for t in (metric.get("tables") or [])]
    present = {t.lower() for t in extract_tables(sql)}
    missing = [table for table in required if table not in present]
    if missing:
        return MetricCheckResult(
            False,
            "SQL is missing required table(s) for metric: " + ", ".join(sorted(missing)),
        )

    outputs = _output_scopes(tree)
    if not outputs:
        return MetricCheckResult(False, "SQL has no result SELECT to check against the metric.")

    column = metric.get("column")
    agg = metric.get("agg")
    time_col = metric.get("time_column")
    for output in outputs:
        scopes = _governing_scopes(output)
        if column and agg and not _has_agg_on_column(scopes, agg, column):
            return MetricCheckResult(
                False,
                f"Metric expects {agg}({column}), but SQL does not clearly express that aggregate.",
            )
        if time_col and not _time_bounds_ok(scopes, time_col):
            return MetricCheckResult(
                False,
                f"Metric time_column '{time_col}' must use half-open bounds "
                "against @start_date and @end_date (no hardcoded date literals).",
            )
        for filt in metric.get("filters") or []:
            if not _filter_ok(scopes, filt):
                return MetricCheckResult(
                    False,
                    f"Metric requires filter on {filt.get('column')} ({filt.get('op')} {filt.get('value')}), "
                    "but SQL WHERE clause does not satisfy it.",
                )
        group_by = metric.get("group_by")
        if group_by and not _group_by_ok(scopes, group_by):
            return MetricCheckResult(
                False,
                f"Metric requires GROUP BY {group_by}, but SQL does not include those columns.",
            )
        if not _joins_ok(scopes, required):
            return MetricCheckResult(
                False,
                "Metric reads multiple tables and requires an equijoin. CROSS JOIN is not allowed.",
            )

    return MetricCheckResult(True, "Metric alignment checks passed.")
