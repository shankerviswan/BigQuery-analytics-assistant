from __future__ import annotations

from pathlib import Path
import logging
import yaml

from app.llm import make_provider, SQLProposal
from app.bigquery_client import BigQueryExecutor, QueryRejected
from app.date_context import build_date_context
from app.schema import fetch_schema_context, STATIC_SCHEMA
from app.metric_check import check_metric_alignment

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]


def load_yaml(path):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class AnalyticsAssistant:
    """Domain-agnostic grounded analytics assistant.

    Tables and metrics come from config/*.yaml — swap them for your real domain.
    thelook_ecommerce in the default config is REFERENCE sample data only.
    """

    def __init__(self, settings, provider=None, executor=None, date_ctx=None):
        self.settings = settings
        self.date_ctx = date_ctx or build_date_context(settings.business_timezone)
        self.provider = provider or make_provider(settings, date_ctx=self.date_ctx)
        self.executor = executor or BigQueryExecutor(
            project_id=settings.project_id,
            location=settings.location,
            max_bytes_billed=settings.max_bytes_billed,
            timeout_seconds=settings.timeout_seconds,
            max_result_rows=settings.max_result_rows,
        )
        self.allowed_tables = set(
            load_yaml(ROOT / "config/allowed_tables.yaml")["allowed_tables"]
        )
        metrics_doc = load_yaml(ROOT / "config/metrics.yaml")
        self.metrics = metrics_doc.get("metrics") or {}
        self.metrics_version = str(metrics_doc.get("version") or "0")

    def _schema_context(self) -> tuple[str, str]:
        """Returns (schema_text, source) where source is live|static."""
        if not self.settings.live_schema:
            return STATIC_SCHEMA, "static"
        try:
            text = fetch_schema_context(
                self.executor.client,
                self.allowed_tables,
                location=self.settings.location,
            )
            if "schema lookup failed" in text and all(
                "schema lookup failed" in line for line in text.splitlines() if line.strip()
            ):
                logger.warning("Live schema lookup failed for all tables; using static fallback")
                return STATIC_SCHEMA, "static"
            return text, "live"
        except Exception as exc:
            logger.warning("Schema context failed (%s); using static fallback", type(exc).__name__)
            return STATIC_SCHEMA, "static"

    def _bind_dates(self, proposal: SQLProposal, metric: dict | None = None) -> dict:
        """Resolve time_range via trusted clock.

        When the metric declares a time_column, a time_range is required and any
        LLM-supplied start_date/end_date are ignored so the trusted clock wins.
        """
        params = dict(proposal.parameters or {})
        needs_time = bool(metric and metric.get("time_column"))
        if needs_time:
            params.pop("start_date", None)
            params.pop("end_date", None)
            if not proposal.time_range:
                raise QueryRejected(
                    "Metric requires a time range. Provide time_range "
                    "(e.g. last_month, january_2024) so the service can bind dates."
                )
        if proposal.time_range:
            try:
                start, end = self.date_ctx.resolve(proposal.time_range)
                params["start_date"] = self.date_ctx.as_timestamp(start)
                params["end_date"] = self.date_ctx.as_timestamp(end)
            except ValueError as exc:
                raise QueryRejected(str(exc)) from exc
        return params

    def ask(self, question: str) -> dict:
        schema_context, schema_source = self._schema_context()
        metrics_context = yaml.safe_dump(
            {"version": self.metrics_version, "metrics": self.metrics},
            sort_keys=True,
        )
        date_prompt = self.date_ctx.to_prompt()

        proposal: SQLProposal = self.provider.propose_sql(
            question,
            schema_context,
            metrics_context,
            date_context=date_prompt,
        )

        base_meta = {
            "schema_source": schema_source,
            "metrics_version": self.metrics_version,
            "date_context": {
                "timezone": self.date_ctx.timezone,
                "today": self.date_ctx.today.isoformat(),
            },
        }

        if proposal.clarification:
            return {
                "status": "clarification_required",
                "question": proposal.clarification,
                **base_meta,
            }
        if not proposal.sql:
            return {
                "status": "refused",
                "reason": "The model did not provide SQL or a clarification.",
                **base_meta,
            }
        if not proposal.metric_id:
            return {
                "status": "refused",
                "reason": "metric_id is required. Ad-hoc unaligned SQL is not allowed in this PoC.",
                "proposed_sql": proposal.sql,
                **base_meta,
            }
        if proposal.metric_id not in self.metrics:
            return {
                "status": "refused",
                "reason": f"Unapproved metric_id: {proposal.metric_id}",
                "proposed_sql": proposal.sql,
                **base_meta,
            }

        metric_def = {**self.metrics[proposal.metric_id], "id": proposal.metric_id}
        alignment = check_metric_alignment(proposal.sql, metric_def)
        if not alignment.ok:
            return {
                "status": "refused",
                "reason": alignment.reason,
                "proposed_sql": proposal.sql,
                "metric_id": proposal.metric_id,
                **base_meta,
            }

        try:
            params = self._bind_dates(proposal, metric_def)
        except QueryRejected as exc:
            return {
                "status": "rejected",
                "reason": str(exc),
                "proposed_sql": proposal.sql,
                "metric_id": proposal.metric_id,
                **base_meta,
            }

        try:
            result = self.executor.execute(proposal.sql, params, self.allowed_tables)
        except QueryRejected as exc:
            return {
                "status": "rejected",
                "reason": str(exc),
                "proposed_sql": proposal.sql,
                "metric_id": proposal.metric_id,
                **base_meta,
            }

        result["metric_id"] = proposal.metric_id
        result["metric_definition"] = self.metrics.get(proposal.metric_id or "", {})
        result["metrics_version"] = self.metrics_version
        result["schema_source"] = schema_source
        result["date_context"] = {
            "timezone": self.date_ctx.timezone,
            "today": self.date_ctx.today.isoformat(),
            "time_range": proposal.time_range,
            "parameters": params,
        }
        result["answer"] = self._format_answer(result)
        result["alignment_note"] = alignment.reason
        return result

    @staticmethod
    def _format_answer(result: dict) -> str:
        rows = result.get("rows") or []
        if not rows:
            return "The query executed successfully but returned no rows."
        if result.get("truncated"):
            return (
                f"Query returned {len(rows)} row(s), cut by the row cap. "
                "That length is not the full count. Review the returned rows for the partial result."
            )
        if len(rows) == 1 and len(rows[0]) == 1:
            key, value = next(iter(rows[0].items()))
            return f"{key}: {value}"
        return (
            f"Query returned {len(rows)} row(s). "
            "Review the returned rows for the breakdown."
        )
