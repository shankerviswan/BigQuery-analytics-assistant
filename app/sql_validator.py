from dataclasses import dataclass
import sqlglot
from sqlglot import exp


@dataclass
class ValidationResult:
    ok: bool
    reason: str = ""


def normalize_table_name(table: exp.Table) -> str:
    parts = [part.name for part in table.parts]
    return ".".join(parts).lower()


def _cte_names(tree: exp.Expression) -> set[str]:
    """Return CTE alias names defined in the query."""
    names: set[str] = set()
    for cte in tree.find_all(exp.CTE):
        alias = cte.alias_or_name
        if alias:
            names.add(str(alias).lower())
    return names


def _is_cte_ref(table: exp.Table, cte_names: set[str]) -> bool:
    """True only for unqualified references to a CTE alias.

    Qualified names (project.dataset.table) are always treated as physical
    tables, even if the last segment matches a CTE alias. This blocks the
    bypass: WITH users AS (...) SELECT * FROM `project.dataset.users`.
    """
    if table.db or table.catalog:
        return False
    return table.name.lower() in cte_names


def _physical_tables(tree: exp.Expression) -> set[str]:
    cte_names = _cte_names(tree)
    return {
        normalize_table_name(t)
        for t in tree.find_all(exp.Table)
        if not _is_cte_ref(t, cte_names)
    }


def validate_sql(sql: str, allowed_tables: set[str]) -> ValidationResult:
    if not sql or not sql.strip():
        return ValidationResult(False, "SQL is empty.")
    try:
        statements = sqlglot.parse(sql, read="bigquery")
    except Exception as exc:
        return ValidationResult(False, f"SQL parse failed: {exc}")
    if len(statements) != 1 or statements[0] is None:
        return ValidationResult(False, "Exactly one SQL statement is allowed.")
    tree = statements[0]

    allowed_roots = (exp.Select, exp.With, exp.Union, exp.Intersect, exp.Except)
    if not isinstance(tree, allowed_roots):
        return ValidationResult(
            False,
            "Only a single read-only SELECT (optionally WITH/UNION/INTERSECT/EXCEPT) is allowed.",
        )

    forbidden = (
        exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop,
        exp.Alter, exp.Command, exp.Merge,
    )
    if any(tree.find(node_type) is not None for node_type in forbidden):
        return ValidationResult(False, "Write or administrative SQL is not allowed.")

    tables = _physical_tables(tree)
    if not tables:
        return ValidationResult(False, "Query must reference an approved table.")

    allowed = {t.lower() for t in allowed_tables}
    unauthorized = tables - allowed
    if unauthorized:
        return ValidationResult(False, "Unauthorized table(s): " + ", ".join(sorted(unauthorized)))

    for dot in tree.find_all(exp.Dot):
        left = getattr(dot.this, "name", None)
        right = getattr(dot.expression, "name", None)
        if str(left).upper() in {"AI", "ML"} and right:
            return ValidationResult(False, f"Disallowed operation: {str(left).upper()}.{str(right).upper()}")
    for fn in tree.find_all(exp.Anonymous):
        name = (fn.name or "").upper()
        if name in {"EXTERNAL_QUERY", "EXECUTE IMMEDIATE"} or name.startswith(("ML.", "AI.")):
            return ValidationResult(False, f"Disallowed operation: {name}")
    for fn in tree.find_all(exp.Func):
        name = (fn.sql_name() if hasattr(fn, "sql_name") else getattr(fn, "name", "") or "").upper()
        if name.startswith(("ML.", "AI.")) or name in {"EXTERNAL_QUERY"}:
            return ValidationResult(False, f"Disallowed operation: {name}")

    upper = sql.upper()
    for banned in ("EXTERNAL_QUERY", "EXECUTE IMMEDIATE"):
        if banned in upper:
            return ValidationResult(False, f"Disallowed operation: {banned}")

    return ValidationResult(True, "SQL passed basic PoC policy checks.")


def extract_tables(sql: str) -> list[str]:
    """Return sorted unique physical table names (unqualified CTE refs excluded)."""
    try:
        tree = sqlglot.parse_one(sql, read="bigquery")
    except Exception:
        return []
    return sorted(_physical_tables(tree))
