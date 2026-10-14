from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.date_context import DateContext, build_date_context


@dataclass
class SQLProposal:
    sql: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)
    clarification: str | None = None
    metric_id: str | None = None


class DemoProvider:
    """Deterministic offline provider for architecture tests and demos."""

    def __init__(self, date_ctx: DateContext | None = None):
        self.date_ctx = date_ctx or build_date_context()

    def propose_sql(
        self,
        question: str,
        schema_context: str,
        metrics_context: str,
        date_context: str = "",
    ) -> SQLProposal:
        q = question.lower()
        if "sales" in q or "revenue" in q:
            return SQLProposal(
                clarification=(
                    "When you say sales/revenue, do you mean "
                    "(a) order_count, (b) cancelled_order_count, "
                    "(c) item_sales_value (sum of sale_price excluding cancelled/returned), "
                    "or (d) category_item_sales?"
                )
            )
        if "cancel" in q:
            start, end = self.date_ctx.january_of(2025)
            return SQLProposal(
                sql="""SELECT COUNT(DISTINCT o.id) AS cancelled_order_count
FROM `bigquery-public-data.thelook_ecommerce.orders` AS o
WHERE LOWER(o.status) = 'cancelled'
  AND o.created_at >= @start_date
  AND o.created_at < @end_date""",
                parameters={"start_date": start.isoformat(), "end_date": end.isoformat()},
                metric_id="cancelled_order_count",
            )
        if "order" in q and ("how many" in q or "count" in q or "created" in q):
            # Prefer explicit year if mentioned, else last_month from trusted clock
            if "2025" in q or "january" in q:
                start, end = self.date_ctx.january_of(2025)
            else:
                start, end = self.date_ctx.last_month()
            return SQLProposal(
                sql="""SELECT COUNT(DISTINCT o.id) AS order_count
FROM `bigquery-public-data.thelook_ecommerce.orders` AS o
WHERE o.created_at >= @start_date
  AND o.created_at < @end_date""",
                parameters={"start_date": start.isoformat(), "end_date": end.isoformat()},
                metric_id="order_count",
            )
        return SQLProposal(
            clarification=(
                "I cannot map that question to an approved metric in this PoC. "
                "Please choose order count, cancelled order count, or clarify the intended sales definition."
            )
        )


class OpenAICompatibleProvider:
    """Adapter for providers exposing an OpenAI-compatible Chat Completions API."""

    def __init__(self, base_url: str, api_key: str, model: str):
        from openai import OpenAI

        self.client = OpenAI(base_url=base_url, api_key=api_key)
        self.model = model

    def propose_sql(
        self,
        question: str,
        schema_context: str,
        metrics_context: str,
        date_context: str = "",
    ) -> SQLProposal:
        system = (
            "You propose BigQuery GoogleSQL only; you do not execute it. "
            "Use only the provided schema and approved metric definitions. "
            "If the metric, date range, timezone, or required filters are ambiguous, ask a clarification. "
            "Never invent columns or metric formulas. "
            "Return JSON with keys: sql, parameters, clarification, metric_id. "
            "parameters must contain only scalar values (prefer ISO dates for date params). "
            "Use the trusted date context for relative periods; if a relative period is not covered, ask for clarification. "
            "Always prefer half-open date ranges (start inclusive, end exclusive)."
        )
        user_content = question
        if date_context:
            user_content = date_context + "\n\nUser question: " + question

        response = self.client.chat.completions.create(
            model=self.model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        system
                        + "\n\nSCHEMA:\n"
                        + schema_context
                        + "\n\nMETRICS:\n"
                        + metrics_context
                    ),
                },
                {"role": "user", "content": user_content},
            ],
        )
        data = json.loads(response.choices[0].message.content or "{}")
        return SQLProposal(
            sql=data.get("sql"),
            parameters=data.get("parameters") or {},
            clarification=data.get("clarification"),
            metric_id=data.get("metric_id"),
        )


def make_provider(settings, date_ctx: DateContext | None = None):
    if settings.llm_base_url and settings.llm_api_key and settings.llm_model:
        return OpenAICompatibleProvider(
            settings.llm_base_url, settings.llm_api_key, settings.llm_model
        )
    return DemoProvider(date_ctx=date_ctx)
