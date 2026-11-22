"""FAQ knowledge base: loads the Google Sheet (public CSV), parses it with pandas
and retrieves the FAQs most relevant to a customer's question.

The sheet is the single source of truth. Nothing from it is hardcoded here; the
only local copy is an on-disk cache of the last successful download, used when
Google Sheets is unreachable.
"""
from __future__ import annotations

import difflib
import io
import logging
import math
import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

from config import IST

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = ("category", "question", "answer")
RETRY_AFTER_FAILURE = 60  # seconds to wait before hitting a failing sheet again
STRONG_SCORE = 0.55  # minimum relevance for an FAQ to be trusted as an answer


class KnowledgeBaseError(Exception):
    """Raised when FAQ data cannot be loaded or is malformed."""


@dataclass(frozen=True)
class FAQ:
    category: str
    question: str
    answer: str


@dataclass(frozen=True)
class SearchResult:
    faq: FAQ
    score: float


@dataclass(frozen=True)
class KBStatus:
    state: str  # "ok" | "stale" | "cached" | "unavailable"
    message: str
    faq_count: int = 0
    loaded_at: datetime | None = None


# --------------------------------------------------------------------------
# Text processing
# --------------------------------------------------------------------------
_PHRASES = [
    (re.compile(r"cash[\s-]*on[\s-]*delivery"), " cod "),
    (re.compile(r"pre[\s-]*orders?(ed|ing)?"), " preorder "),
    (re.compile(r"e[\s-]*books?"), " ebook "),
    (re.compile(r"(second|2nd)[\s-]*hand|pre[\s-]*loved|\bused books?"), " preloved "),
    (re.compile(r"gift[\s-]*wrap\w*|\bwrap(ping|ped)?\b|\bgift\w*"), " giftwrap "),
    (re.compile(r"pin[\s-]*code|\bzip\b|postal code"), " pincode "),
    (re.compile(r"money[\s-]*back|reimburse\w*"), " refund "),
    (re.compile(r"\bsend\w*\b.{0,20}\bback\b"), " return "),
    (re.compile(r"\bsend\w*\b.{0,30}\b(home|house|doorstep|address)\b"), " delivery "),
    (re.compile(r"how (many )?(days|long)|how soon|\beta\b|time ?line"), " long "),
]
_SYNONYMS = {
    "shipping": "delivery", "shipment": "delivery", "ship": "delivery", "dispatch": "delivery",
    "courier": "delivery", "parcel": "delivery", "arrive": "delivery", "arrival": "delivery",
    "deliver": "delivery", "reach": "delivery", "post": "delivery",
    "charge": "cost", "charg": "cost", "charges": "cost", "price": "cost", "fee": "cost", "fare": "cost", "much": "cost",
    "pay": "payment", "paying": "payment", "upi": "payment", "card": "payment",
    "method": "method", "option": "method", "mode": "method",
    "promo": "coupon", "voucher": "coupon", "discount": "coupon", "code": "coupon",
    "forgot": "password", "login": "password", "reset": "password",
    "timing": "hours", "open": "hours", "days": "long", "duration": "long",
    "edit": "change", "modify": "change", "update": "change", "amend": "change",
    "broken": "damaged", "torn": "damaged", "defective": "damaged", "misprinted": "damaged",
    "incorrect": "wrong", "tracking": "track", "trace": "track",
    "cancellation": "cancel", "kindle": "ebook", "digital": "ebook",
}
_STOPWORDS = set(
    """a an the and or but of to in on at for from with by is are was were be been am do does did
    can could would should will shall may might i me my mine we our us you your it its this that
    these those what which who whom whose how when where why if then so as than too very just also
    there here any some please pls tell know want need like get got help kindly hello hi hey thanks
    thank wondering whether about regarding available yes no ok okay let rs rupees give let's
    take takes taking usually actually really much many one new item items offer provide accept possible request process apply sell buy buying bought purchase purchased""".split()
) - {"take", "new", "item", "sell"}  # these words do carry meaning in some FAQ questions
_STOPWORDS.discard("get")


_TIME_WORDS = {"long", "take"}


def _stem(word: str) -> str:
    if len(word) <= 3 or word.isdigit():
        return word
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def tokenize(text: str) -> list[str]:
    """Lower-case, normalise phrases/synonyms, drop stop-words and light-stem."""
    text = unicodedata.normalize("NFKC", str(text)).lower().replace("₹", " rs ")
    for pattern, replacement in _PHRASES:
        text = pattern.sub(replacement, text)
    tokens: list[str] = []
    for raw in re.findall(r"[a-z0-9]+", text):
        if raw in _STOPWORDS:
            continue
        token = _SYNONYMS.get(raw) or _stem(raw)
        token = _SYNONYMS.get(token, token)
        if token and token not in _STOPWORDS:
            tokens.append(token)
    return tokens


# --------------------------------------------------------------------------
# The knowledge base
# --------------------------------------------------------------------------
@dataclass
class KnowledgeBase:
    faqs: list[FAQ]
    status: KBStatus
    _fields: list[dict] = field(default_factory=list, repr=False)
    _idf: dict[str, float] = field(default_factory=dict, repr=False)
    _vocab: set[str] = field(default_factory=set, repr=False)

    def __post_init__(self) -> None:
        df: dict[str, int] = {}
        for faq in self.faqs:
            parts = {
                "q": set(tokenize(faq.question)),
                "c": set(tokenize(faq.category)),
                "a": set(tokenize(faq.answer)),
            }
            self._fields.append(parts)
            for token in parts["q"] | parts["c"] | parts["a"]:
                df[token] = df.get(token, 0) + 1
        n = len(self.faqs)
        topic = {t for faq in self.faqs for t in tokenize(faq.category)}
        self._topic = topic
        self._idf = {}
        for t, c in df.items():
            value = math.log(1 + (n + 1) / (c + 0.5))
            # category words (delivery, refund...) carry the topic; "long"/"take" are filler
            self._idf[t] = value * (2.0 if t in topic else 0.5 if t in _TIME_WORDS else 1.0)
        self._vocab = set(df)
        self._unseen_idf = math.log(1 + (n + 1) / 0.5)

    def __len__(self) -> int:
        return len(self.faqs)

    @property
    def categories(self) -> list[str]:
        return sorted({f.category for f in self.faqs})

    def by_category(self, category: str) -> list[FAQ]:
        return [f for f in self.faqs if f.category.lower() == category.lower()]

    def _query_tokens(self, query: str) -> list[str]:
        out = []
        for token in dict.fromkeys(tokenize(query)):
            if token not in self._vocab and len(token) >= 5:  # tolerate typos
                close = difflib.get_close_matches(token, self._vocab, n=1, cutoff=0.85)
                token = close[0] if close else token
            out.append(token)
        return out

    def search(self, query: str, top_k: int = 3) -> list[SearchResult]:
        """Return up to ``top_k`` FAQs, best first. Score = share (0-1) of the
        question's informative words that the FAQ explains."""
        tokens = self._query_tokens(query)
        if not tokens or not self.faqs:
            return []
        total = sum(self._idf.get(t, self._unseen_idf * 0.6) for t in tokens)
        scored: list[SearchResult] = []
        for faq, parts in zip(self.faqs, self._fields):
            matched, in_core = 0.0, False
            for t in tokens:
                weight = 1.0 if t in parts["q"] else 0.7 if t in parts["c"] else 0.45 if t in parts["a"] else 0
                if weight:
                    matched += self._idf.get(t, 0) * weight
                    in_core = in_core or weight > 0.45
            score = matched / total if total else 0.0
            if in_core and score > 0:
                scored.append(SearchResult(faq, round(score, 3)))
        scored.sort(key=lambda r: r.score, reverse=True)
        if not scored:
            return []
        cutoff = scored[0].score * 0.6
        return [r for r in scored if r.score >= cutoff][:top_k]

    def has_topic(self, query: str) -> bool:
        """True if the query names a topic itself (delivery, refund, ...), so it is not a bare follow-up."""
        return any(t in self._topic for t in tokenize(query))

    def relevant(self, query: str, top_k: int = 3) -> list[SearchResult]:
        """Like ``search`` but returns [] unless the best match is trustworthy."""
        results = self.search(query, top_k)
        return results if results and results[0].score >= STRONG_SCORE else []

    @classmethod
    def from_csv_text(cls, text: str, status: KBStatus | None = None) -> "KnowledgeBase":
        faqs = parse_faq_csv(text)
        status = status or KBStatus("ok", "FAQ knowledge base ready", len(faqs), datetime.now(IST))
        return cls(faqs, status)

    @classmethod
    def unavailable(cls, message: str) -> "KnowledgeBase":
        return cls([], KBStatus("unavailable", message, 0, None))


def parse_faq_csv(text: str) -> list[FAQ]:
    """Validate and parse CSV text into FAQs. Raises KnowledgeBaseError."""
    stripped = text.lstrip("﻿ \r\n\t")
    if stripped[:1] == "<":
        raise KnowledgeBaseError("Received a web page instead of CSV (is the sheet public?)")
    try:
        df = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)
    except Exception as exc:
        raise KnowledgeBaseError(f"FAQ CSV could not be parsed: {exc}") from exc
    df.columns = [str(c).strip().lower() for c in df.columns]
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise KnowledgeBaseError(f"FAQ CSV is missing column(s): {', '.join(missing)}")
    faqs = []
    for row in df.itertuples(index=False):
        d = row._asdict() if hasattr(row, "_asdict") else {}
        q, a = str(d.get("question", "")).strip(), str(d.get("answer", "")).strip()
        if q and a:
            faqs.append(FAQ(str(d.get("category", "")).strip() or "General", q, a))
    if not faqs:
        raise KnowledgeBaseError("FAQ CSV has no usable rows")
    return faqs


def normalize_csv_url(url: str) -> str:
    """Accept a normal Google Sheets link (…/edit) and turn it into a CSV export link."""
    match = re.match(r"(https://docs\.google\.com/spreadsheets/d/[\w-]+)/(edit|view)", url.strip())
    if not match:
        return url.strip()
    gid = re.search(r"gid=(\d+)", url)
    return f"{match.group(1)}/export?format=csv" + (f"&gid={gid.group(1)}" if gid else "")


# --------------------------------------------------------------------------
# Loading with caching
# --------------------------------------------------------------------------
class _Loader:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.kb: KnowledgeBase | None = None
        self.fetched_at = 0.0
        self.retry_at = 0.0


_LOADERS: dict[str, _Loader] = {}


def _fetch(url: str, timeout: int) -> str:
    try:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": "PaperLeafSupportAgent/1.0"})
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise KnowledgeBaseError(f"Could not reach Google Sheets: {type(exc).__name__}") from exc
    return resp.content.decode("utf-8-sig", errors="replace")


def get_knowledge_base(
    url: str,
    *,
    cache_path: Path | None = None,
    ttl: int = 300,
    timeout: int = 10,
    force: bool = False,
) -> KnowledgeBase:
    """Return the knowledge base, re-reading the sheet when the cache is older
    than ``ttl`` seconds (or when ``force``). Never raises."""
    url = normalize_csv_url(url)
    loader = _LOADERS.setdefault(url, _Loader())
    with loader.lock:
        now = time.time()
        fresh = loader.kb is not None and loader.kb.status.state == "ok" and now - loader.fetched_at < ttl
        if fresh and not force:
            return loader.kb  # type: ignore[return-value]
        if not force and loader.kb is not None and now < loader.retry_at:
            return loader.kb
        try:
            text = _fetch(url, timeout)
            kb = KnowledgeBase.from_csv_text(text)
            loader.kb, loader.fetched_at = kb, now
            if cache_path:
                try:
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    cache_path.write_text(text, encoding="utf-8")
                except OSError:
                    logger.warning("Could not write FAQ cache file")
            return kb
        except KnowledgeBaseError as exc:
            logger.warning("FAQ load failed: %s", exc)
            loader.retry_at = now + RETRY_AFTER_FAILURE
            return _fallback(loader, cache_path)


def _fallback(loader: _Loader, cache_path: Path | None) -> KnowledgeBase:
    if loader.kb is not None and loader.kb.faqs:
        old = loader.kb.status
        loader.kb = KnowledgeBase(
            loader.kb.faqs,
            KBStatus("stale", "Couldn't refresh the FAQ sheet; using the last loaded copy", old.faq_count, old.loaded_at),
        )
        return loader.kb
    if cache_path and cache_path.exists():
        try:
            faqs = parse_faq_csv(cache_path.read_text(encoding="utf-8"))
            loader.kb = KnowledgeBase(
                faqs, KBStatus("cached", "Google Sheets unreachable; using a saved copy", len(faqs), None)
            )
            return loader.kb
        except (OSError, KnowledgeBaseError):
            pass
    loader.kb = KnowledgeBase.unavailable("FAQ sheet unavailable right now")
    return loader.kb
