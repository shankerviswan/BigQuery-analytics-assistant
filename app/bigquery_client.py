from __future__ import annotations

from app.sql_validator import validate_sql, extract_tables


class QueryRejected(Exception):
    pass


def _infer_param_type(name: str, value) -> str:
    """Infer BigQuery parameter types.

    start_date/end_date are TIMESTAMP instants already resolved in the business
    timezone (midnight at the boundary). A DATE parameter cannot be compared
    directly to a TIMESTAMP column. Only those two names are forced to
    TIMESTAMP — birth_date and other *_date names stay STRING.
    """
    n = name.lower()
    if n in {"start_date", "end_date"}:
        return "TIMESTAMP"
    if n.endswith("_ts") or n in {"start", "end"}:
        return "TIMESTAMP"
    if isinstance(value, bool):
        return "BOOL"
    if isinstance(value, int) and not isinstance(value, bool):
        return "INT64"
    if isinstance(value, float):
        return "FLOAT64"
    return "STRING"


class BigQueryExecutor:
    def __init__(
        self,
        project_id: str,
        location: str,
        max_bytes_billed: int,
        timeout_seconds: int,
        max_result_rows: int,
        client=None,
    ):
        self.project_id = project_id
        self.location = location
        self.max_bytes_billed = max_bytes_billed
        self.timeout_seconds = timeout_seconds
        self.max_result_rows = max_result_rows
        self._client = client

    @property
    def client(self):
        if self._client is None:
            from google.cloud import bigquery
            self._client = bigquery.Client(project=self.project_id, location=self.location)
        return self._client

    def execute(self, sql: str, parameters: dict, allowed_tables: set[str]) -> dict:
        from google.cloud import bigquery
        from google.api_core.exceptions import GoogleAPICallError

        policy = validate_sql(sql, allowed_tables)
        if not policy.ok:
            raise QueryRejected(policy.reason)

        query_parameters = []
        for name, value in (parameters or {}).items():
            if not name.replace("_", "").isalnum():
                raise QueryRejected(f"Invalid parameter name: {name}")
            ptype = _infer_param_type(name, value)
            bind_val = value
            if ptype == "DATE" and isinstance(value, str) and len(value) >= 10:
                bind_val = value[:10]
            query_parameters.append(
                bigquery.ScalarQueryParameter(
                    name,
                    ptype,
                    str(bind_val) if ptype in {"STRING", "TIMESTAMP", "DATE"} else value,
                )
            )

        dry_config = bigquery.QueryJobConfig(
            dry_run=True,
            use_query_cache=False,
            query_parameters=query_parameters,
        )
        try:
            dry_job = self.client.query(sql, job_config=dry_config, location=self.location)
        except GoogleAPICallError as exc:
            raise QueryRejected(f"Dry-run failed: {exc}") from exc

        estimated_bytes = int(dry_job.total_bytes_processed or 0)
        if estimated_bytes > self.max_bytes_billed:
            raise QueryRejected(
                f"Dry-run estimate {estimated_bytes} bytes exceeds limit {self.max_bytes_billed}."
            )

        run_config = bigquery.QueryJobConfig(
            use_legacy_sql=False,
            maximum_bytes_billed=self.max_bytes_billed,
            use_query_cache=True,
            query_parameters=query_parameters,
        )
        try:
            job = self.client.query(sql, job_config=run_config, location=self.location)
            rows_iter = job.result(
                timeout=self.timeout_seconds, max_results=self.max_result_rows + 1
            )
            rows = [dict(row.items()) for row in rows_iter]
        except GoogleAPICallError as exc:
            raise QueryRejected(f"Query execution failed: {exc}") from exc

        truncated = len(rows) > self.max_result_rows
        rows = rows[: self.max_result_rows]

        return {
            "status": "succeeded",
            "job_id": job.job_id,
            "sql": sql,
            "parameters": parameters or {},
            "source_tables": extract_tables(sql),
            "estimated_bytes_processed": estimated_bytes,
            "bytes_processed": int(job.total_bytes_processed or 0),
            "bytes_billed": int(job.total_bytes_billed or 0),
            "cache_hit": bool(job.cache_hit),
            "rows": rows,
            "truncated": truncated,
        }
