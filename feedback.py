"""'Was this helpful?' answers, saved to a local CSV (feedback.csv)."""
from __future__ import annotations

import csv
import logging
import threading
from datetime import datetime
from pathlib import Path

from config import IST

logger = logging.getLogger(__name__)

FIELDNAMES = ["timestamp", "rating", "intent", "question", "reply"]
_LOCK = threading.Lock()


def _safe_cell(value: str) -> str:
    """Stop spreadsheet apps treating text as a formula (CSV injection)."""
    value = " ".join((value or "").split())
    return "'" + value if value[:1] in ("=", "+", "-", "@") else value


def save_feedback(
    path: Path, rating: str, question: str = "", reply: str = "", intent: str = "", now: datetime | None = None
) -> bool:
    """Append one row. Returns True only if it was really written; never raises."""
    if rating not in ("yes", "no"):
        return False
    now = now or datetime.now(IST)
    try:
        with _LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            new_file = not path.exists() or path.stat().st_size == 0
            with path.open("a", newline="", encoding="utf-8-sig" if new_file else "utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
                if new_file:
                    writer.writeheader()
                writer.writerow({
                    "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
                    "rating": rating,
                    "intent": _safe_cell(intent),
                    "question": _safe_cell(question)[:500],
                    "reply": _safe_cell(reply)[:500],
                })
        return True
    except Exception:  # disk full, permissions, file open elsewhere...
        logger.exception("Could not save feedback")
        return False
