"""Support tickets: unique IDs (PLB-YYYYMMDD-NNN) stored in a local CSV file."""
from __future__ import annotations

import csv
import logging
import re
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from config import IST, TICKET_CATEGORIES

logger = logging.getLogger(__name__)

FIELDNAMES = [
    "ticket_id", "created_at", "customer_name", "customer_email",
    "category", "description", "priority", "status",
]
_LOCK = threading.Lock()
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class TicketError(Exception):
    """Raised with a customer-safe message when a ticket can't be created."""


@dataclass(frozen=True)
class Ticket:
    ticket_id: str
    created_at: str
    customer_name: str
    customer_email: str
    category: str
    description: str
    priority: str
    status: str = "open"


def validate_email(email: str) -> bool:
    return bool(_EMAIL.match(email)) and len(email) <= 120


def _safe_cell(value: str) -> str:
    """Stop spreadsheet apps treating text as a formula (CSV injection)."""
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def _read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def _next_id(path: Path, now: datetime) -> str:
    prefix = f"PLB-{now:%Y%m%d}-"
    seqs = [
        int(r["ticket_id"][len(prefix):])
        for r in _read_rows(path)
        if r.get("ticket_id", "").startswith(prefix) and r["ticket_id"][len(prefix):].isdigit()
    ]
    return f"{prefix}{(max(seqs) + 1 if seqs else 1):03d}"


def create_ticket(
    *,
    category: str,
    description: str,
    path: Path,
    customer_name: str = "",
    customer_email: str = "",
    priority: str = "normal",
    now: datetime | None = None,
) -> Ticket:
    """Create and persist a ticket. Raises TicketError (never returns a ticket
    unless it was really written and read back)."""
    description = " ".join((description or "").split())
    name = " ".join((customer_name or "").split())[:80]
    email = (customer_email or "").strip()
    if len(description) < 5:
        raise TicketError("Please add a short description of the issue so our team can help.")
    if email and not validate_email(email):
        raise TicketError("That email address doesn't look right. Please check it or leave it blank.")
    if category not in TICKET_CATEGORIES:
        category = "General question"
    priority = "high" if priority == "high" else "normal"
    now = now or datetime.now(IST)

    try:
        with _LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            ticket = Ticket(
                _next_id(path, now), now.strftime("%Y-%m-%d %H:%M:%S"), name, email,
                category, description[:2000], priority,
            )
            new_file = not path.exists() or path.stat().st_size == 0
            with path.open("a", newline="", encoding="utf-8-sig" if new_file else "utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
                if new_file:
                    writer.writeheader()
                writer.writerow({
                    "ticket_id": ticket.ticket_id, "created_at": ticket.created_at,
                    "customer_name": _safe_cell(ticket.customer_name),
                    "customer_email": _safe_cell(ticket.customer_email),
                    "category": ticket.category, "description": _safe_cell(ticket.description),
                    "priority": ticket.priority, "status": ticket.status,
                })
                fh.flush()
            if not any(r.get("ticket_id") == ticket.ticket_id for r in _read_rows(path)):
                raise OSError("ticket not found after write")
    except TicketError:
        raise
    except Exception as exc:  # disk full, permissions, locked file...
        logger.exception("Ticket creation failed")
        raise TicketError(
            "I couldn't save your ticket just now. Please try again in a moment."
        ) from exc
    return ticket
