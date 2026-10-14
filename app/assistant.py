from __future__ import annotations

from pathlib import Path
import yaml

from app.llm import make_provider, SQLProposal
from app.bigquery_client import BigQueryExecutor, QueryRejected
from app.date_context import build_date_context
from app.schema import fetch_schema_context, STATIC_SCHEMA
from app.metric_check import check_metric_alignment

ROOT = Path(__file__).resolve().parents[1]


def load_yaml(path):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class AnalyticsAssistant:
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
        self.metrics = load_yaml(ROOT / "config/metrics.yaml")["metrics"]

    def _schema_context(self) -> str:
        if not self.settings.live_schema:
            return STATIC_SCHEMA
        try:
            return fetch_schema_context(
                self.executor.client,
                self.allowed_tables,
                location=self.settings.location,
            )
        except Exception:
            return STATIC_SCHEMA

    def ask(self, question: str) -> dict:
        schema_context = self._schema_context()
        metrics_context = yaml.safe_dump(self.metrics, sort_keys=True)
        date_prompt = self.date_ctx.to_prompt()

        proposal: SQLProposal = self.provider.propose_sql(
            question,
            schema_context,
            metrics_context,
            date_context=date_prompt,
        )

        if proposal.clarification:
            return {
                "status": "clarification_required",
                "question": proposal.clarification,
                "date_context": {
                    "timezone": self.date_ctx.timezone,
                    "today": self.date_ctx.today.isoformat(),
                },
            }
        if not proposal.sql:
            return {
                "status": "refused",
                "reason": "The model did not provide SQL or a clarification.",
            }
        if proposal.metric_id and proposal.metric_id not in self.metrics:
            return {
                "status": "refused",
                "reason": "The model selected an unapproved metric.",
            }

        metric_def = self.metrics.get(proposal.metric_id or "", {})
        # Inject id for the checker convenience
        if proposal.metric_id and "id" not in metric_def:
            metric_def = {**metric_def, "id": proposal.metric_id}

        alignment = check_metric_alignment(proposal.sql, metric_def if metric_def else None)
        if not alignment.ok:
            return {
                "status": "refused",
                "reason": alignment.reason,
                "proposed_sql": proposal.sql,
                "metric_id": proposal.metric_id,
            }

        try:
            result = self.executor.execute(
                proposal.sql, proposal.parameters or {}, self.allowed_tables
            )
        except QueryRejected as exc:
            return {
                "status": "rejected",
                "reason": str(exc),
                "proposed_sql": proposal.sql,
                "metric_id": proposal.metric_id,
            }

        result["metric_id"] = proposal.metric_id
        result["metric_definition"] = self.metrics.get(proposal.metric_id or "", {})
        result["date_context"] = {
            "timezone": self.date_ctx.timezone,
            "today": self.date_ctx.today.isoformat(),
            "parameters": proposal.parameters or {},
        }
        result["answer"] = self._format_answer(result)
        result["alignment_note"] = alignment.reason
        return result

    @staticmethod
    def _format_answer(result: dict) -> str:
        # Deliberately deterministic: no LLM can add unsupported numbers.
        if not result.get("rows"):
            return "The query executed successfully but returned no rows."
        if len(result["rows"]) == 1 and len(result["rows"][0]) == 1:
            key, value = next(iter(result["rows"][0].items()))
            return f"{key}: {value}"
        return (
            f"Query returned {len(result['rows'])} row(s). "
            "Review the returned rows for the breakdown."
        )
