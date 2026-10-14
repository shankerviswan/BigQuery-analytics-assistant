"""Live schema introspection limited to the allow-listed tables."""
from __future__ import annotations


def _table_parts(full_name: str) -> tuple[str, str, str]:
    parts = full_name.split(".")
    if len(parts) != 3:
        raise ValueError(f"Expected project.dataset.table, got: {full_name}")
    return parts[0], parts[1], parts[2]


def fetch_schema_context(client, allowed_tables: set[str], location: str = "US") -> str:
    """Return a compact schema string for the LLM. Allow-listed tables only."""
    from google.cloud import bigquery

    lines: list[str] = []
    for full_name in sorted(allowed_tables):
        try:
            project, dataset, table = _table_parts(full_name)
            sql = f"""
            SELECT column_name, data_type, is_nullable
            FROM `{project}.{dataset}.INFORMATION_SCHEMA.COLUMNS`
            WHERE table_name = @table_name
            ORDER BY ordinal_position
            """
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("table_name", "STRING", table)
                ],
                maximum_bytes_billed=50_000_000,
            )
            rows = list(client.query(sql, job_config=job_config, location=location).result())
            cols = ", ".join(
                f"{r.column_name}:{r.data_type}" + ("?" if r.is_nullable == "YES" else "")
                for r in rows
            )
            lines.append(f"{full_name}({cols})")
        except Exception as exc:  # pragma: no cover
            lines.append(f"{full_name} (schema lookup failed: {type(exc).__name__})")
    if not lines:
        return "No allow-listed tables available."
    return "\n".join(lines)


STATIC_SCHEMA = (
    "bigquery-public-data.thelook_ecommerce.orders("
    "id:INT64, user_id:INT64, status:STRING, created_at:TIMESTAMP, "
    "shipped_at:TIMESTAMP, delivered_at:TIMESTAMP, returned_at:TIMESTAMP);\n"
    "bigquery-public-data.thelook_ecommerce.order_items("
    "id:INT64, order_id:INT64, user_id:INT64, product_id:INT64, "
    "status:STRING, created_at:TIMESTAMP, sale_price:FLOAT64);\n"
    "bigquery-public-data.thelook_ecommerce.products("
    "id:INT64, category:STRING, name:STRING, brand:STRING, retail_price:FLOAT64, cost:FLOAT64)"
)
