"""Admin dashboard data access: reading, filtering and summarising tickets.csv / feedback.csv.

Pure functions only (no HTTP, no auth) so they're easy to test on their own. The web layer
(webapp.py) wires these to the /api/admin/* routes.
"""
from __future__ import annotations

import csv
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from ticket_system import FIELDNAMES as TICKET_FIELDNAMES  # noqa: F401 (re-exported for callers)
from ticket_system import _read_rows as read_tickets  # internal reuse: same module family, no public reader exists yet

FEEDBACK_FIELDNAMES = ["timestamp", "rating", "intent", "question", "reply"]


def read_feedback(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def filter_tickets(
    rows: list[dict], *, status: str | None = None, priority: str | None = None,
    category: str | None = None, q: str | None = None,
) -> list[dict]:
    out = rows
    if status:
        out = [r for r in out if r.get("status") == status]
    if priority:
        out = [r for r in out if r.get("priority") == priority]
    if category:
        out = [r for r in out if r.get("category") == category]
    if q:
        needle = q.strip().lower()
        out = [
            r for r in out
            if needle in r.get("description", "").lower()
            or needle in r.get("customer_name", "").lower()
            or needle in r.get("customer_email", "").lower()
            or needle in r.get("ticket_id", "").lower()
        ]
    return out


def filter_feedback(rows: list[dict], *, rating: str | None = None, intent: str | None = None) -> list[dict]:
    out = rows
    if rating:
        out = [r for r in out if r.get("rating") == rating]
    if intent:
        out = [r for r in out if r.get("intent") == intent]
    return out


def tickets_summary(rows: list[dict]) -> dict:
    return {
        "by_status": dict(Counter(r.get("status", "") for r in rows)),
        "by_priority": dict(Counter(r.get("priority", "") for r in rows)),
        "by_category": dict(Counter(r.get("category", "") for r in rows)),
    }


def feedback_summary(rows: list[dict]) -> dict:
    yes = sum(1 for r in rows if r.get("rating") == "yes")
    no = sum(1 for r in rows if r.get("rating") == "no")
    total = yes + no
    return {
        "yes": yes,
        "no": no,
        "yes_rate": round(yes / total, 4) if total else 0.0,
        "by_intent": dict(Counter(r.get("intent", "") for r in rows)),
    }


def _parse_date(value: str) -> str | None:
    """First 10 chars of a 'YYYY-MM-DD HH:MM:SS' timestamp, or None if unparsable."""
    try:
        return datetime.strptime(value[:19], "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%d")
    except ValueError:
        return None


def daily_trend(ticket_rows: list[dict], feedback_rows: list[dict], days: int = 30, *, today: datetime | None = None) -> list[dict]:
    """Zero-filled per-day counts for the last `days` days (oldest first), by created_at/timestamp."""
    today = today or datetime.now()
    dates = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days - 1, -1, -1)]
    tickets_by_day = Counter(d for r in ticket_rows if (d := _parse_date(r.get("created_at", ""))))
    feedback_by_day = Counter(d for r in feedback_rows if (d := _parse_date(r.get("timestamp", ""))))
    return [{"date": d, "tickets": tickets_by_day.get(d, 0), "feedback": feedback_by_day.get(d, 0)} for d in dates]
