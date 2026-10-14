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
    # BigQuery SELECT queries can contain CTEs, but writes and scripting are disallowed.
    if not isinstance(tree, (exp.Select, exp.With)):
        return ValidationResult(False, "Only a single SELECT query is allowed.")
    forbidden = (
        exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop,
        exp.Alter, exp.Command, exp.Merge,
    )
    if any(tree.find(node_type) is not None for node_type in forbidden):
        return ValidationResult(False, "Write or administrative SQL is not allowed.")
    tables = {normalize_table_name(t) for t in tree.find_all(exp.Table)}
    if not tables:
        return ValidationResult(False, "Query must reference an approved table.")
    allowed = {t.lower() for t in allowed_tables}
    unauthorized = tables - allowed
    if unauthorized:
        return ValidationResult(False, "Unauthorized table(s): " + ", ".join(sorted(unauthorized)))
    # Block common external/side-effect surfaces in this small PoC.
    for node in tree.find_all(exp.Func):
        name = node.sql_name().upper()
        if name in {"EXECUTE IMMEDIATE", "LOAD", "EXPORT", "REMOTE_FUNCTION"}:
            return ValidationResult(False, f"Disallowed function or operation: {name}")
    return ValidationResult(True, "SQL passed basic PoC policy checks.")


def extract_tables(sql: str) -> list[str]:
    """Return sorted unique table names referenced by the SQL (from AST)."""
    try:
        tree = sqlglot.parse_one(sql, read="bigquery")
    except Exception:
        return []
    return sorted({
        normalize_table_name(t) for t in tree.find_all(exp.Table)
    })
