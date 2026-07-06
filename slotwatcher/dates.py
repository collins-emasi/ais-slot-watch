from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable
import re

ISO_DATE_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
MONTH_FIRST_RE = re.compile(
    r"\b((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+\d{1,2},\s+20\d{2})\b",
    re.IGNORECASE,
)
DAY_FIRST_RE = re.compile(
    r"\b(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+20\d{2})\b",
    re.IGNORECASE,
)

FORMATS = (
    "%Y-%m-%d",
    "%B %d, %Y",
    "%b %d, %Y",
    "%d %B %Y",
    "%d %b %Y",
)

LIKELY_DATE_KEYS = {
    "date",
    "appointment_date",
    "available_date",
    "available_dates",
    "first_available_date",
    "earliest_date",
}


def parse_one_date(value: str) -> date | None:
    value = value.strip()
    value = value.replace("Sept", "Sep")
    for fmt in FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def extract_dates_from_text(text: str) -> set[date]:
    found: set[date] = set()
    for regex in (ISO_DATE_RE, MONTH_FIRST_RE, DAY_FIRST_RE):
        for match in regex.findall(text or ""):
            parsed = parse_one_date(match)
            if parsed:
                found.add(parsed)
    return found


def extract_dates_from_json(value: Any, *, parent_key: str = "") -> set[date]:
    """Recursively extract date-like values from JSON returned by the appointment calendar.

    This intentionally scans strings anywhere in the payload, but dates are later filtered
    to the user's target window to avoid false positives from old announcements.
    """
    found: set[date] = set()

    if isinstance(value, dict):
        for key, child in value.items():
            key_lower = str(key).lower()
            if key_lower in LIKELY_DATE_KEYS and isinstance(child, str):
                parsed = parse_one_date(child)
                if parsed:
                    found.add(parsed)
            found.update(extract_dates_from_json(child, parent_key=key_lower))
        return found

    if isinstance(value, list):
        for child in value:
            found.update(extract_dates_from_json(child, parent_key=parent_key))
        return found

    if isinstance(value, str):
        found.update(extract_dates_from_text(value))

    return found


def filter_target_dates(
    dates: Iterable[date],
    *,
    earliest_allowed: date,
    current_appointment: date,
    latest_allowed: date | None = None,
) -> list[date]:
    latest = latest_allowed or current_appointment
    return sorted(
        {
            d
            for d in dates
            if earliest_allowed <= d < current_appointment and d <= latest
        }
    )
