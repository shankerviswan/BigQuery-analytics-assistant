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
    names: set[str] = set()
    for w in tree.find_all(exp.With):
        for cte in w.expressions:
            alias = getattr(cte, "alias", None) or getattr(cte, "alias_or_name", None)
            if alias:
                names.add(str(alias).lower())
            if hasattr(cte, "alias_or_name") and cte.alias_or_name:
                names.add(str(cte.alias_or_name).lower())
            if isinstance(cte, exp.CTE) and cte.alias:
                names.add(cte.alias.lower())
    return names


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

    allowed_roots = (exp.Select, exp.With, exp.Union)
    if not isinstance(tree, allowed_roots):
        return ValidationResult(False, "Only a single read-only SELECT (optionally WITH/UNION) is allowed.")

    forbidden = (
        exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop,
        exp.Alter, exp.Command, exp.Merge,
    )
    if any(tree.find(node_type) is not None for node_type in forbidden):
        return ValidationResult(False, "Write or administrative SQL is not allowed.")

    cte_aliases = _cte_names(tree)
    tables = {normalize_table_name(t) for t in tree.find_all(exp.Table)}
    tables -= cte_aliases
    tables = {t for t in tables if t not in cte_aliases and t.split(".")[-1] not in cte_aliases}

    if not tables:
        return ValidationResult(False, "Query must reference an approved table.")

    allowed = {t.lower() for t in allowed_tables}
    unauthorized = tables - allowed
    if unauthorized:
        return ValidationResult(False, "Unauthorized table(s): " + ", ".join(sorted(unauthorized)))

    upper = sql.upper()
    for banned in ("EXTERNAL_QUERY", "EXECUTE IMMEDIATE", "ML.PREDICT", "REMOTE_FUNCTION"):
        if banned in upper:
            return ValidationResult(False, f"Disallowed operation: {banned}")

    return ValidationResult(True, "SQL passed basic PoC policy checks.")


def extract_tables(sql: str) -> list[str]:
    """Return sorted unique physical table names (CTE aliases excluded)."""
    try:
        tree = sqlglot.parse_one(sql, read="bigquery")
    except Exception:
        return []
    cte_aliases = _cte_names(tree)
    tables = {normalize_table_name(t) for t in tree.find_all(exp.Table)}
    tables = {
        t for t in tables
        if t not in cte_aliases and t.split(".")[-1] not in cte_aliases
    }
    return sorted(tables)
