"""PaperLeaf Books website + Paige chatbot API.

Run:  python -m uvicorn webapp:app --port 8000      then open http://localhost:8000

This file is only a thin web layer. Paige herself (agent.py, guardrails.py, knowledge_base.py, ticket_system.py,
feedback.py) is reused exactly as it is for the Streamlit app, so the FAQ, rules, escalation and tickets are identical.
"""
from __future__ import annotations

import hmac
import logging
import re
import secrets
import time
from collections import OrderedDict, defaultdict, deque
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import admin as admin_data
import feedback as feedback_store
import guardrails as gr
from agent import GREETING, GeminiClient, PaigeAgent, TicketOffer
from book_catalog import BookCatalog
from config import BASE_DIR, TICKET_CATEGORIES, get_settings
from knowledge_base import get_knowledge_base

logger = logging.getLogger(__name__)
SITE_DIR = BASE_DIR / "site"

# One-click questions shown on the page and in the chat. (Only the questions live here; answers always come from the FAQ sheet.)
TOPICS = [
    {"icon": "🚚", "title": "Delivery", "question": "How long does delivery take?"},
    {"icon": "💳", "title": "Payments", "question": "Which payment methods can I use?"},
    {"icon": "↩️", "title": "Returns", "question": "What is your return policy?"},
    {"icon": "💸", "title": "Refunds", "question": "When will I get my refund?"},
    {"icon": "📚", "title": "Pre-orders", "question": "Can I pre-order a new book?"},
    {"icon": "🎁", "title": "Gift wrapping", "question": "Can I gift wrap a book?"},
]
QUICK_REPLIES = [
    {"label": "🚚 Delivery cost", "message": "How much does delivery cost?"},
    {"label": "↩️ Return policy", "message": "What is your return policy?"},
    {"label": "📦 Track my order", "message": "How do I track my order?"},
    {"label": "🙋 Talk to a person", "message": "I want to speak to a human."},
]


# --------------------------------------------------------------------------
# Small in-memory rate limiter (one server process; use Redis if you ever run several)
# --------------------------------------------------------------------------
class Limiter:
    def __init__(self, max_hits: int, window: int) -> None:
        self.max_hits, self.window = max_hits, window
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> int:
        """Record a hit. Returns 0 if allowed, else seconds to wait."""
        now, q = time.monotonic(), self.hits[key]
        while q and now - q[0] >= self.window:
            q.popleft()
        if len(q) >= self.max_hits:
            return max(1, int(self.window - (now - q[0]) + 0.999))
        q.append(now)
        return 0

    def clear(self) -> None:
        self.hits.clear()


chat_limit = Limiter(max_hits=20, window=60)
ticket_limit = Limiter(max_hits=10, window=3600)
feedback_limit = Limiter(max_hits=60, window=60)
admin_login_limit = Limiter(max_hits=5, window=300)

# Tickets already created in a browser session, so confirming the same issue twice never makes a duplicate.
_seen_tickets: OrderedDict[str, dict] = OrderedDict()
_SESSION = re.compile(r"^[A-Za-z0-9_\-]{8,64}$")

# Admin dashboard sessions: opaque token -> expiry (epoch seconds). One process, in memory, same
# pattern as _seen_tickets above - fine for a single shared passcode, no need for a real user store.
_ADMIN_SESSIONS: dict[str, float] = {}
_ADMIN_COOKIE = "admin_session"

_llm: GeminiClient | None = None


def build_agent() -> PaigeAgent:
    """A Paige for one request. The sheet is cached, and the Gemini client is created once and reused."""
    global _llm
    settings = get_settings()
    kb = get_knowledge_base(settings.faq_csv_url, cache_path=settings.faq_cache_path, ttl=settings.faq_cache_ttl, timeout=settings.faq_timeout)
    if settings.llm_configured and (_llm is None or _llm.api_key != settings.google_api_key or _llm.model != settings.gemini_model):
        _llm = GeminiClient(settings.google_api_key, settings.gemini_model, settings.llm_timeout_ms)
    return PaigeAgent(kb, settings, llm=_llm if settings.llm_configured else None)


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------
class HistoryItem(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=2000)
    intent: str | None = Field(default=None, max_length=24)


class ChatIn(BaseModel):
    message: str = Field(max_length=2000)
    history: list[HistoryItem] = Field(default_factory=list, max_length=20)


class OfferOut(BaseModel):
    category: str
    priority: str
    description: str
    reason: str = ""


class ChatOut(BaseModel):
    text: str
    intent: str
    sources: list[str] = []
    offer: OfferOut | None = None
    notice: str | None = None
    user_text: str  # what the customer typed, with card numbers / OTPs / passwords masked


class TicketIn(BaseModel):
    session_id: str
    category: str = Field(max_length=60)
    priority: Literal["normal", "high"] = "normal"
    description: str = Field(max_length=2000)
    reason: str = Field(default="", max_length=200)
    name: str = Field(default="", max_length=80)
    email: str = Field(default="", max_length=120)


class FeedbackIn(BaseModel):
    rating: Literal["yes", "no"]
    question: str = Field(default="", max_length=2000)
    reply: str = Field(default="", max_length=4000)
    intent: str = Field(default="", max_length=24)


class BookOut(BaseModel):
    title: str
    author: str
    price: float
    in_stock: bool
    description: str


class AdminLoginIn(BaseModel):
    passcode: str = Field(max_length=200)


class TicketOut(BaseModel):
    ticket_id: str
    created_at: str
    customer_name: str
    customer_email: str
    category: str
    description: str
    priority: str
    status: str


class FeedbackOut(BaseModel):
    timestamp: str
    rating: str
    intent: str
    question: str
    reply: str


class TicketsSummaryOut(BaseModel):
    by_status: dict[str, int]
    by_priority: dict[str, int]
    by_category: dict[str, int]


class FeedbackSummaryOut(BaseModel):
    yes: int
    no: int
    yes_rate: float
    by_intent: dict[str, int]


class TrendPointOut(BaseModel):
    date: str
    tickets: int
    feedback: int


class AnalyticsOut(BaseModel):
    tickets: TicketsSummaryOut
    feedback: FeedbackSummaryOut
    trend: list[TrendPointOut]


# --------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------
app = FastAPI(title="PaperLeaf Books – Paige", version="1.0.0", docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")

_CSP = (
    "default-src 'self'; style-src 'self' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
    "img-src 'self' data:; connect-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
)


@app.middleware("http")
async def security(request: Request, call_next):
    # Block cross-site POSTs (browsers always send Origin on those).
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") != f"{request.url.scheme}://{request.headers.get('host', '')}":
            return JSONResponse({"detail": "Cross-site request blocked."}, status_code=403)
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    if not request.url.path.startswith("/api/docs") and not request.url.path.startswith("/api/openapi"):
        response.headers.setdefault("Content-Security-Policy", _CSP)
    return response


def _ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _limit(limiter: Limiter, key: str, what: str) -> None:
    if wait := limiter.check(key):
        raise HTTPException(status_code=429, detail=f"You're doing that a bit fast ({what}). Please wait {wait} seconds and try again.", headers={"Retry-After": str(wait)})


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/config")
def config() -> dict:
    """Everything the page needs. Nothing secret."""
    settings = get_settings()
    return {
        "greeting": GREETING,
        "topics": TOPICS,
        "quick_replies": QUICK_REPLIES,
        "ticket_categories": TICKET_CATEGORIES,
        "max_message_chars": settings.max_message_chars,
    }


@app.get("/api/books", response_model=list[BookOut])
def books() -> list[dict]:
    """The sample catalog, so the homepage can show what's in stock."""
    catalog = BookCatalog(get_settings().books_csv_path)
    return [b.__dict__ for b in catalog.all()]


@app.post("/api/chat", response_model=ChatOut)
def chat(body: ChatIn, request: Request) -> ChatOut:
    _limit(chat_limit, _ip(request), "messages")
    history = [h.model_dump() for h in body.history[-12:]]
    reply = build_agent().respond(body.message, history)
    offer = OfferOut(**reply.offer.__dict__) if reply.offer else None
    return ChatOut(
        text=reply.text, intent=reply.intent, sources=reply.sources, offer=offer, notice=reply.notice,
        user_text=gr.redact_sensitive(gr.normalize_input(body.message)),
    )


@app.post("/api/tickets", status_code=201)
def create_ticket(body: TicketIn, request: Request) -> dict:
    """Creates the ticket through the existing ticket system. Success is reported only if it was really saved."""
    _limit(ticket_limit, _ip(request), "tickets")
    if not _SESSION.match(body.session_id):
        raise HTTPException(status_code=422, detail="Invalid session.")
    seen = _seen_tickets.setdefault(body.session_id, {})
    _seen_tickets.move_to_end(body.session_id)
    while len(_seen_tickets) > 2000:
        _seen_tickets.popitem(last=False)
    offer = TicketOffer(category=body.category, priority=body.priority, description=body.description, reason=body.reason)
    reply = build_agent().submit_ticket(offer, name=body.name, email=body.email, description=body.description, seen=seen)
    if not reply.ticket_id:
        raise HTTPException(status_code=422, detail=reply.text)
    return {"ok": True, "ticket_id": reply.ticket_id, "text": reply.text}


@app.post("/api/feedback", status_code=201)
def send_feedback(body: FeedbackIn, request: Request) -> dict:
    _limit(feedback_limit, _ip(request), "feedback")
    saved = feedback_store.save_feedback(
        get_settings().feedback_path, body.rating, gr.redact_sensitive(body.question), gr.redact_sensitive(body.reply), body.intent
    )
    if not saved:
        raise HTTPException(status_code=503, detail="We couldn't save your feedback just now.")
    return {"ok": True}


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(SITE_DIR / "index.html")


@app.get("/admin", include_in_schema=False)
def admin_page() -> FileResponse:
    return FileResponse(SITE_DIR / "admin.html")


# --------------------------------------------------------------------------
# Admin dashboard: tickets, feedback and basic analytics, behind a shared passcode.
# --------------------------------------------------------------------------
def require_admin(request: Request) -> None:
    token = request.cookies.get(_ADMIN_COOKIE)
    expiry = _ADMIN_SESSIONS.get(token or "")
    if not token or expiry is None or expiry < time.time():
        raise HTTPException(status_code=401, detail="Please log in to the admin dashboard.")


@app.post("/api/admin/login")
def admin_login(body: AdminLoginIn, response: Response, request: Request) -> dict:
    settings = get_settings()
    if not settings.app_passcode:
        raise HTTPException(status_code=503, detail="The admin dashboard isn't configured yet.")
    _limit(admin_login_limit, _ip(request), "login attempts")
    if not hmac.compare_digest(body.passcode, settings.app_passcode):
        raise HTTPException(status_code=401, detail="Incorrect passcode.")
    token = secrets.token_urlsafe(32)
    _ADMIN_SESSIONS[token] = time.time() + settings.admin_session_ttl
    response.set_cookie(
        _ADMIN_COOKIE, token, httponly=True, samesite="strict", secure=request.url.scheme == "https",
        max_age=settings.admin_session_ttl,
    )
    return {"ok": True}


@app.post("/api/admin/logout")
def admin_logout(response: Response, request: Request) -> dict:
    _ADMIN_SESSIONS.pop(request.cookies.get(_ADMIN_COOKIE, ""), None)
    response.delete_cookie(_ADMIN_COOKIE)
    return {"ok": True}


@app.get("/api/admin/me", dependencies=[Depends(require_admin)])
def admin_me() -> dict:
    return {"ok": True}


@app.get("/api/admin/tickets", response_model=list[TicketOut], dependencies=[Depends(require_admin)])
def admin_tickets(
    status: str | None = Query(default=None, max_length=20),
    priority: str | None = Query(default=None, max_length=20),
    category: str | None = Query(default=None, max_length=60),
    q: str | None = Query(default=None, max_length=200),
) -> list[dict]:
    rows = admin_data.read_tickets(get_settings().tickets_path)
    return admin_data.filter_tickets(rows, status=status, priority=priority, category=category, q=q)


@app.get("/api/admin/feedback", response_model=list[FeedbackOut], dependencies=[Depends(require_admin)])
def admin_feedback(
    rating: str | None = Query(default=None, max_length=10),
    intent: str | None = Query(default=None, max_length=24),
) -> list[dict]:
    rows = admin_data.read_feedback(get_settings().feedback_path)
    return admin_data.filter_feedback(rows, rating=rating, intent=intent)


@app.get("/api/admin/analytics", response_model=AnalyticsOut, dependencies=[Depends(require_admin)])
def admin_analytics() -> dict:
    settings = get_settings()
    tickets = admin_data.read_tickets(settings.tickets_path)
    feedback_rows = admin_data.read_feedback(settings.feedback_path)
    return {
        "tickets": admin_data.tickets_summary(tickets),
        "feedback": admin_data.feedback_summary(feedback_rows),
        "trend": admin_data.daily_trend(tickets, feedback_rows),
    }


app.mount("/assets", StaticFiles(directory=BASE_DIR / "assets"), name="assets")
app.mount("/static", StaticFiles(directory=SITE_DIR), name="static")
