"""Trusted date / timezone context. Never let the LLM invent 'now'."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class DateContext:
    """Half-open intervals: start inclusive, end exclusive."""
    timezone: str
    today: date
    now_iso: str

    def last_month(self) -> tuple[date, date]:
        first_of_this = self.today.replace(day=1)
        end = first_of_this
        start = (first_of_this - timedelta(days=1)).replace(day=1)
        return start, end

    def this_month(self) -> tuple[date, date]:
        start = self.today.replace(day=1)
        if start.month == 12:
            end = date(start.year + 1, 1, 1)
        else:
            end = date(start.year, start.month + 1, 1)
        return start, end

    def january_of(self, year: int) -> tuple[date, date]:
        return date(year, 1, 1), date(year, 2, 1)

    def to_prompt(self) -> str:
        lm_s, lm_e = self.last_month()
        tm_s, tm_e = self.this_month()
        return (
            f"Trusted clock (do not invent dates):\n"
            f"- timezone: {self.timezone}\n"
            f"- today: {self.today.isoformat()}\n"
            f"- now: {self.now_iso}\n"
            f"- last_month (half-open): {lm_s.isoformat()} .. {lm_e.isoformat()}\n"
            f"- this_month (half-open): {tm_s.isoformat()} .. {tm_e.isoformat()}\n"
            f"Always use half-open intervals: start inclusive, end exclusive.\n"
            f"If the user asks for a relative period not listed, ask for clarification."
        )


def build_date_context(tz_name: str = "America/Los_Angeles") -> DateContext:
    """Server-side trusted clock. Default TZ is a common US business default;
    override via settings in production.
    """
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = timezone.utc
        tz_name = "UTC"
    now = datetime.now(tz)
    return DateContext(
        timezone=tz_name,
        today=now.date(),
        now_iso=now.isoformat(timespec="seconds"),
    )
