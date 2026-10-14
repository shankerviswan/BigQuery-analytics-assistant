from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from app.date_context import DateContext, build_date_context


def _extract_month_kind(q: str) -> str | None:
    """Parse 'january 2024' / 'jan 2023' into january_YYYY. No hardcoded year."""
    m = re.search(r"\b(january|jan)\s+(20\d{2})\b", q, re.I)
    if m:
        return f"january_{m.group(2)}"
    m = re.search(r"\b(20\d{2})[-/](0?1)\b", q)
    if m:
        return f"january_{m.group(1)}"
    return None


@dataclass
class SQLProposal:
    sql: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)
    clarification: str | None = None
    metric_id: str | None = None
    time_range: str | None = None


class DemoProvider:
    """Deterministic offline provider for architecture tests and demos.

    Uses thelook sample only as REFERENCE data — not a product domain assumption.
    """

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
            tr = _extract_month_kind(q) or "last_month"
            return SQLProposal(
                sql="""SELECT COUNT(DISTINCT o.order_id) AS cancelled_order_count
FROM `bigquery-public-data.thelook_ecommerce.orders` AS o
WHERE LOWER(o.status) = 'cancelled'
  AND o.created_at >= @start_date
  AND o.created_at < @end_date""",
                metric_id="cancelled_order_count",
                time_range=tr,
            )
        if "order" in q and ("how many" in q or "count" in q or "created" in q):
            tr = _extract_month_kind(q) or "last_month"
            return SQLProposal(
                sql="""SELECT COUNT(DISTINCT o.order_id) AS order_count
FROM `bigquery-public-data.thelook_ecommerce.orders` AS o
WHERE o.created_at >= @start_date
  AND o.created_at < @end_date""",
                metric_id="order_count",
                time_range=tr,
            )
        return SQLProposal(
            clarification=(
                "I cannot map that question to an approved metric in this PoC. "
                "Please choose an approved metric or clarify the intended definition."
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
            "Return JSON with keys: sql, parameters, clarification, metric_id, time_range. "
            "Prefer time_range as one of: last_month, this_month, last_quarter, january_YYYY, month_YYYY_MM "
            "and leave start_date/end_date for the service to bind from the trusted clock. "
            "Always use half-open date ranges in SQL: col >= @start_date AND col < @end_date. "
            "Never hardcode date literals on the metric time column. "
            "metric_id is required when proposing SQL."
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
            time_range=data.get("time_range"),
        )


def make_provider(settings, date_ctx: DateContext | None = None):
    if settings.llm_base_url and settings.llm_api_key and settings.llm_model:
        return OpenAICompatibleProvider(
            settings.llm_base_url, settings.llm_api_key, settings.llm_model
        )
    return DemoProvider(date_ctx=date_ctx)
