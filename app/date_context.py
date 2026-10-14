"""Trusted date / timezone context. Python resolves ranges; the LLM must not invent 'now'."""
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

    def last_quarter(self) -> tuple[date, date]:
        q = (self.today.month - 1) // 3
        if q == 0:
            start = date(self.today.year - 1, 10, 1)
            end = date(self.today.year, 1, 1)
        else:
            start = date(self.today.year, 3 * (q - 1) + 1, 1)
            end = date(self.today.year, 3 * q + 1, 1)
        return start, end

    def month(self, year: int, month: int) -> tuple[date, date]:
        start = date(year, month, 1)
        if month == 12:
            end = date(year + 1, 1, 1)
        else:
            end = date(year, month + 1, 1)
        return start, end

    def resolve(self, kind: str, year: int | None = None, month: int | None = None) -> tuple[date, date]:
        """Resolve a structured time_range kind into half-open dates."""
        k = (kind or "").lower().replace(" ", "_")
        if k in {"last_month", "previous_month"}:
            return self.last_month()
        if k in {"this_month", "current_month"}:
            return self.this_month()
        if k in {"last_quarter", "previous_quarter"}:
            return self.last_quarter()
        if k.startswith("january_"):
            y = k.split("_")[-1]
            if y.isdigit():
                return self.month(int(y), 1)
        if k == "january":
            if year is None:
                raise ValueError("january requires an explicit year (e.g. january_2024)")
            return self.month(year, 1)
        if k.startswith("month_"):
            parts = k.split("_")
            if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
                return self.month(int(parts[1]), int(parts[2]))
        if year is not None and month is not None:
            return self.month(year, month)
        raise ValueError(f"Unsupported time_range kind: {kind}")

    def to_prompt(self) -> str:
        lm_s, lm_e = self.last_month()
        tm_s, tm_e = self.this_month()
        lq_s, lq_e = self.last_quarter()
        return (
            f"Trusted clock (do not invent dates):\n"
            f"- timezone: {self.timezone}\n"
            f"- today: {self.today.isoformat()}\n"
            f"- now: {self.now_iso}\n"
            f"- last_month (half-open): {lm_s.isoformat()} .. {lm_e.isoformat()}\n"
            f"- this_month (half-open): {tm_s.isoformat()} .. {tm_e.isoformat()}\n"
            f"- last_quarter (half-open): {lq_s.isoformat()} .. {lq_e.isoformat()}\n"
            f"Prefer returning time_range as last_month, this_month, last_quarter, "
            f"january_YYYY, or month_YYYY_MM and let the service bind dates.\n"
            f"Always use half-open intervals with @start_date / @end_date.\n"
            f"Never hardcode date literals for the metric time column.\n"
            f"If the relative period is not listed, ask for clarification."
        )


def build_date_context(tz_name: str = "America/Los_Angeles") -> DateContext:
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
