"""Central configuration for PaperLeaf Books – Paige.

Values come from (in order): real environment variables / a local ``.env`` file,
then Streamlit secrets (used on Streamlit Community Cloud). Nothing secret is
hardcoded here, and secrets are excluded from ``repr`` so they never leak into logs.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")  # silently does nothing if the file is missing

APP_NAME = "PaperLeaf Books"
AGENT_NAME = "Paige"
PAGE_TITLE = "PaperLeaf Books Support"
LOGO_PATH = BASE_DIR / "assets" / "logo.png"

# India Standard Time has no daylight saving, so a fixed offset is enough.
IST = timezone(timedelta(hours=5, minutes=30), "IST")

# Public FAQ sheet (CSV export). Override with FAQ_CSV_URL.
DEFAULT_FAQ_CSV_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1sRaJ3wVQUy2omRVjrUhTTVzY9fK8ytCyRumZQZ9kb5Y/export?format=csv"
)
DEFAULT_MODEL = "gemini-3.1-flash-lite"

TICKET_CATEGORIES = [
    "General question",
    "Delivery",
    "Payments",
    "Order issue",
    "Refunds & returns",
    "Damaged or wrong item",
    "Complaint",
    "Speak to a person",
]


def _from_streamlit_secrets(name: str) -> str | None:
    try:
        import streamlit as st

        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:  # no secrets file, or not running under Streamlit
        pass
    return None


def _get(name: str, default: str = "") -> str:
    value = os.environ.get(name) or _from_streamlit_secrets(name) or default
    return value.strip()


def _get_int(name: str, default: int) -> int:
    try:
        return int(_get(name, str(default)))
    except ValueError:
        return default


def _get_list(name: str, default: list[str]) -> list[str]:
    value = _get(name)
    return [item.strip() for item in value.split(",") if item.strip()] if value else default


@dataclass(frozen=True)
class Settings:
    google_api_key: str = field(default="", repr=False)
    app_passcode: str = field(default="", repr=False)
    gemini_model: str = DEFAULT_MODEL
    faq_csv_url: str = DEFAULT_FAQ_CSV_URL
    faq_cache_ttl: int = 300  # seconds before the sheet is re-read
    faq_timeout: int = 10
    llm_timeout_ms: int = 20_000
    tickets_path: Path = BASE_DIR / "tickets.csv"
    feedback_path: Path = BASE_DIR / "feedback.csv"
    books_csv_path: Path = BASE_DIR / "books.csv"
    faq_cache_path: Path = BASE_DIR / ".cache" / "faq_cache.csv"
    max_message_chars: int = 1000
    refund_handoff_threshold: int = 2000  # refunds/returns above this (₹) go to a person
    admin_session_ttl: int = 8 * 3600  # seconds an admin login stays valid
    allowed_origins: list[str] = field(default_factory=lambda: ["http://localhost:8000"])

    @property
    def llm_configured(self) -> bool:
        return bool(self.google_api_key)


def get_settings() -> Settings:
    """Read settings fresh each call so edits to .env / secrets are picked up."""
    return Settings(
        google_api_key=_get("GOOGLE_API_KEY") or _get("GEMINI_API_KEY"),
        app_passcode=_get("APP_PASSCODE"),
        gemini_model=_get("GEMINI_MODEL", DEFAULT_MODEL),
        faq_csv_url=_get("FAQ_CSV_URL", DEFAULT_FAQ_CSV_URL),
        faq_cache_ttl=_get_int("FAQ_CACHE_TTL_SECONDS", 300),
        tickets_path=Path(_get("TICKETS_CSV_PATH") or BASE_DIR / "tickets.csv"),
        feedback_path=Path(_get("FEEDBACK_CSV_PATH") or BASE_DIR / "feedback.csv"),
        books_csv_path=Path(_get("BOOKS_CSV_PATH") or BASE_DIR / "books.csv"),
        admin_session_ttl=_get_int("ADMIN_SESSION_TTL_SECONDS", 8 * 3600),
        allowed_origins=_get_list("ALLOWED_ORIGINS", ["http://localhost:8000"]),
    )
