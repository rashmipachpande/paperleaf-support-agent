"""Guardrails: input validation, security checks, scope detection, escalation
rules, output checks and login throttling.

Design: these are plain, deterministic rules that run *before* and *after* the
LLM, so safety never depends on the model behaving. User text is always treated
as untrusted input.
"""
from __future__ import annotations

import hmac
import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# Canned replies (no FAQ facts in here – those come only from the sheet)
# --------------------------------------------------------------------------
OUT_OF_SCOPE_REPLY = (
    "I'm your PaperLeaf Books assistant, so I'm best at helping with bookstore-related questions! 📖 "
    "Feel free to ask me about deliveries, payments, returns, refunds, pre-orders, or gift wrapping."
)
FAQ_MISS_REPLY = (
    "I don't want to give you the wrong information about that. 📚 "
    "Let me connect you with our support team so they can help you properly."
)
REPLIES = {
    "injection": (
        "I'm Paige, your PaperLeaf Books assistant, and I can't change my role or my rules. 📖 "
        "I'd be happy to help with deliveries, payments, returns, refunds, pre-orders or gift wrapping, though!"
    ),
    "secret_request": (
        "I can't share keys, passwords or any internal settings — those stay private. 🔒 "
        "But I'm happy to help with anything about your PaperLeaf Books orders!"
    ),
    "prompt_request": (
        "I keep my internal instructions private, but I'm an open book about the store! 📚 "
        "Ask me about deliveries, payments, returns, refunds, pre-orders or gift wrapping."
    ),
    "privacy": (
        "I can't share anyone else's personal details or orders — and I'd never share yours either. 🔒 "
        "If you have a question about your own order, I'm glad to help."
    ),
    "promise_request": (
        "I'm not able to issue discount codes or free items, or to promise refunds — I can only share what's in our "
        "store policies. 📚 If you have a coupon code, ask me how to apply it!"
    ),
    "sensitive_data": (
        "For your safety, please don't share card numbers, OTPs, passwords or ID numbers here — I'll never need "
        "them. 🔒 I've hidden that message. Feel free to ask your question again without those details."
    ),
    "credentials": (
        "Please don't share OTPs, passwords or card details with me or anyone else — I'll never ask for them. 🔒 "
        "If you have a question about your order or payment, just ask and I'll help from there!"
    ),
    "empty": "It looks like your message was empty. 📝 Type your question and I'll be happy to help!",
    "too_long": "That's a long one! 📜 Could you shorten your message to a few sentences so I can help properly?",
}

# One-line versions, used when a message mixes a blocked request with a valid question.
SHORT_REFUSALS = {
    "injection": "I can't change my role or my rules. 📖",
    "secret_request": "I can't share keys, passwords or internal settings. 🔒",
    "prompt_request": "I keep my internal instructions private. 📚",
    "privacy": "I can't share anyone else's personal details or orders. 🔒",
    "promise_request": "I'm not able to issue discount codes or free items. 📚",
    "credentials": "Please never share OTPs, passwords or card details — I'll never ask for them. 🔒",
}


# --------------------------------------------------------------------------
# Input normalisation / validation
# --------------------------------------------------------------------------
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f​-‏‪-‮⁠﻿]")


def normalize_input(text: str | None) -> str:
    """Strip control/zero-width characters, fold look-alike Unicode, collapse spaces."""
    text = unicodedata.normalize("NFKC", text or "")
    text = _CONTROL.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


_CLAUSE_SPLIT = re.compile(r"(?<=[?.!;])\s+|\s*[;,]\s*|\s+(?:and then|and also|and|also|plus|but)\s+", re.I)


def split_clauses(text: str) -> list[str]:
    """Split a message into its separate requests ('What is X, and how much is Y?')."""
    parts = []
    for piece in _CLAUSE_SPLIT.split(text):
        piece = re.sub(r"^(?:also|and|then|plus|but)\s+", "", (piece or "").strip(" ,.;"), flags=re.I).strip()
        if piece:
            parts.append(piece)
    return parts


def validate_input(text: str, max_chars: int) -> str | None:
    """Return a reply key ('empty'/'too_long') if the message is unusable, else None."""
    if not text:
        return "empty"
    if len(text) > max_chars:
        return "too_long"
    return None


# --------------------------------------------------------------------------
# Security patterns
# --------------------------------------------------------------------------
def _rx(*patterns: str) -> re.Pattern:
    return re.compile("|".join(patterns), re.I)


_INJECTION = _rx(
    r"\b(ignore|disregard|forget|override|bypass|drop)\b.{0,30}\b(instruction|rule|prompt|polic|guideline|restriction|everything|above|previous|prior)",
    r"\b(jailbreak|developer mode|dan mode|do anything now|no restrictions|without restrictions)\b",
    r"\b(new|updated) (rules|instructions)\b",
    r"\bfrom now on\b.{0,40}\b(you are|you're|act|behave|ignore)\b",
    r"\bbreak (your|the) rules\b",
)
_IDENTITY = _rx(
    r"\b(pretend|imagine|roleplay|role-play)\b.{0,30}\b(you are|you're|to be|as)\b",
    r"\bact (as|like)\b",
    r"\byou are (now|no longer)\b",
    r"\byou('re| are) not paige\b",
    r"\b(different|another|new) (agent|assistant|ai|bot|persona)\b",
    r"\bswitch (to|into) .{0,20}(mode|persona|character)\b",
)
_SECRETS = _rx(
    r"\bapi[\s_-]*keys?\b",
    r"\b(secret|access|auth|bearer) ?(keys?|tokens?)\b",
    r"\benv(ironment)?[\s_-]*(var|variable)s?\b",
    r"(^|\s)\.env\b",
    r"\b(reveal|show|tell|share|give|print|display|leak|dump|what('s| is)|read)\b.{0,40}\b(secrets?|credentials?|passcode|app password|admin password|config(uration)?)\b",
)
_PROMPT = _rx(
    r"\b(system|initial|hidden|original|internal)\s+(prompt|instruction|message)s?\b",
    r"\b(show|reveal|repeat|print|display|tell|share|give|output|recite|summari[sz]e)\b.{0,30}\b(your|the) (prompt|instructions|rules|guidelines|programming)\b",
    r"\bwhat (are|were) your (instructions|rules|guidelines)\b",
    r"\brepeat (the|everything|all).{0,20}\b(above|before)\b",
)
_PRIVACY = _rx(
    r"\b(address|phone|number|email|name|details|order|orders)\b.{0,30}\b(of|for)\b.{0,20}\b(another|other|someone else'?s?|previous|a different|the) (customer|person|user|buyer)\b",
    r"\b(another|other|someone else'?s|previous|last) (customer|person|user|buyer)'?s?\b.{0,40}\b(address|phone|number|email|name|details|order)",
    r"\bcustomer who (ordered|bought|purchased)\b",
    r"\bwho (else )?(ordered|bought|purchased)\b",
    r"\b(someone|somebody) else'?s?\b.{0,20}\b(orders?|address|phone|number|email|details|purchases?|account)\b",
    r"\b(all|every|list of all)\b.{0,15}\b(customers|users|buyers)\b",
)
_CREDENTIALS = _rx(
    r"\b(give|send|share|tell|provide|type|submit|text|message|do you (need|want|require|ask for)|need my|should i (give|send|share))\b"
    r".{0,30}\b(otp|one[- ]time (password|code)|cvv|pin|password|card number|credit card|debit card|card details)\b",
)
_PROMISE = _rx(
    r"\b(give|send|generate|create|issue|get|offer|grant|make)\b.{0,25}\b(me|us)\b.{0,25}\b(discount|coupon|promo|voucher|free)\b",
    r"\b\d{1,3} ?% ?(off|discount)\b",
    r"\b(discount|coupon|promo|voucher) code\b.{0,20}\b(for me|please)\b",
    r"\b(guarantee|promise|assure|swear)\b.{0,30}\b(refund|money back|replacement|discount|free)\b",
    r"\bfree (book|books|gift|shipping|delivery)\b.{0,15}\b(please|for me|to me)\b",
    r"\b(give|grant|issue|send)\b.{0,12}\b(discounts?|coupons?|promo codes?|vouchers?)\b",
)


def check_security(text: str) -> str | None:
    """Return the reply key for the first security issue found, or None."""
    for key, rx in (
        ("injection", _INJECTION),
        ("identity", _IDENTITY),
        ("prompt_request", _PROMPT),
        ("secret_request", _SECRETS),
        ("credentials", _CREDENTIALS),
        ("privacy", _PRIVACY),
        ("promise_request", _PROMISE),
    ):
        if rx.search(text):
            return "injection" if key == "identity" else key
    return None


# --------------------------------------------------------------------------
# Sensitive data typed by the customer (cards, OTPs, passwords, IDs)
# --------------------------------------------------------------------------
_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
_SENSITIVE = [
    re.compile(r"\b(otp|one[- ]time (password|code)|verification code)\b\D{0,15}\d{3,8}\b", re.I),
    re.compile(r"\b(cvv|cvc|pin)\b\D{0,10}\d{3,6}\b", re.I),
    re.compile(r"\b(my )?password\s*(is|:|=)\s*\S+", re.I),
    re.compile(r"(?<!\d)\d{4}[ -]\d{4}[ -]\d{4}(?!\d)"),  # Aadhaar-style
    re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"),  # PAN
]


def _luhn(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
        alt = not alt
    return total % 10 == 0


def _card_spans(text: str):
    for m in _CARD.finditer(text):
        digits = re.sub(r"\D", "", m.group())
        if 13 <= len(digits) <= 19 and _luhn(digits):
            yield m.span()


def contains_sensitive_data(text: str) -> bool:
    return any(True for _ in _card_spans(text)) or any(rx.search(text) for rx in _SENSITIVE)


def redact_sensitive(text: str) -> str:
    """Mask card numbers, OTPs, passwords and IDs (used for display and storage)."""
    for start, end in sorted(_card_spans(text), reverse=True):
        text = text[:start] + "[hidden]" + text[end:]
    for rx in _SENSITIVE:
        text = rx.sub("[hidden]", text)
    return text


# --------------------------------------------------------------------------
# Scope detection
# --------------------------------------------------------------------------
_IN_SCOPE = _rx(
    r"\b(books?|novels?|paperleaf|orders?|ordered|deliver\w*|shipp?\w*|courier|parcel|package|payments?|pay|paid|upi|cod|cash|"
    r"refunds?|returns?|exchange|replace\w*|pre-?orders?|gift\w*|wrap\w*|coupon|discount|track\w*|cancel\w*|address|pincode|"
    r"account|password|login|support|ticket|policy|policies|store|price|cost|fees?|charges?|invoice|receipt|damaged|wrong item|"
    r"e-?books?|second-?hand|pre-?loved|complain\w*|human|agent|person|loyalty|rewards?|points|membership|subscription|"
    r"bulk|stock|catalog\w*|author|edition|isbn|sell|buy|purchase|shop|website|offers?|sale|invoice|gst)\b"
)
_OUT_OF_SCOPE = _rx(
    r"\b(python|java(script)?|c\+\+|html|css|sql|code|coding|programming|algorithm|debug|software)\b",
    r"\b(president|prime minister|politic\w*|election|government|minister|party)\b",
    r"\b(weather|temperature|forecast|rain)\b",
    r"\b(poem|poetry|story|joke|song|lyrics|essay|riddle|haiku)\b.{0,20}\b(write|tell|make|compose)\b|\b(write|tell|make|compose)\b.{0,25}\b(poem|poetry|story|joke|song|lyrics|essay|riddle|haiku)\b",
    r"\b(recipe|cook\w*|movie|cricket|football|match score|news|stock|bitcoin|crypto\w*|invest\w*|medical|doctor|symptom|lawyer|legal advice)\b",
    r"\b(math|maths|homework|equation|translate)\b",
    r"\bcapital of\b|\bwho (is|was|won)\b|\bmeaning of\b|\bwhat is (a |an |the )?\w+\??$",
)


def has_in_scope_terms(text: str) -> bool:
    return bool(_IN_SCOPE.search(text))


def has_out_of_scope_terms(text: str) -> bool:
    return bool(_OUT_OF_SCOPE.search(text))


# --------------------------------------------------------------------------
# Escalation rules – when a person should take over
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Escalation:
    category: str  # ticket category (see config.TICKET_CATEGORIES)
    priority: str  # "normal" | "high"
    reason: str
    tags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def human_only(self) -> bool:
        return self.tags == ("human_request",)

    @property
    def upset(self) -> bool:
        return "complaint" in self.tags


_HUMAN = _rx(
    r"\b(speak|talk|chat|connect|transfer|escalate|put)\b.{0,25}\b(human|person|agent|representative|someone|somebody|executive|staff|manager|support team|customer care)\b",
    r"\b(real|live|actual) (human|person|agent)\b",
    r"\b(human|person|agent|representative|manager) (please|pls)\b",
    r"\bi (want|need|would like) (a |an |to )?(human|person|agent|representative)\b",
    r"\bcall me\b|\bcontact (a )?(human|person)\b",
)
_COMPLAINT = _rx(
    r"\b(angry|furious|upset|annoyed|frustrat\w*|disappointed|disgusted|fed up|livid|outraged|unhappy|dissatisfied|irritated)\b",
    r"\b(terrible|horrible|awful|worst|unacceptable|ridiculous|pathetic|useless|disgraceful|scam|fraud|cheat\w*|rip(ped)?[- ]?off)\b",
    r"\bcomplain\w*\b",
    r"\bnever (ordering|shopping|buying) (from you|again)\b",
)
_DAMAGED = _rx(
    r"\b(damaged|damage|broken|torn|ripped|defective|faulty|crushed|soaked|stained|misprinted|missing pages?|pages? (is |are )?missing|leaking)\b",
    r"\b(wrong|incorrect|different) (book|item|product|order|copy|edition|title)\b",
    r"\b(received|got|delivered|sent)\b.{0,12}\b(wrong|incorrect|different)\b",
    r"\bnot what i ordered\b",
)
_REFUND_ISSUE = _rx(
    r"\b(my|the) (refund|money)\b.{0,40}\b(hasn'?t|has not|haven'?t|not|never|didn'?t|still|delay\w*|pending|missing|stuck|late|taking too long|isn'?t|wasn'?t)\b",
    r"\b(haven'?t|have not|didn'?t|did not|never|still (not|haven'?t))\b.{0,25}\b(got|get|received|receive)\b.{0,15}\b(my |the )?(refund|money)\b",
    r"\bwhere('s| is) my (refund|money)\b",
    r"\brefund\b.{0,25}\b(not|hasn'?t|never)\b.{0,15}\b(arrived|received|credited|processed|reflected|come)\b",
)
_ORDER_ISSUE = _rx(
    r"\b(my|the) (order|parcel|package|shipment|book|delivery)\b.{0,40}\b(late|delayed|lost|missing|stuck|not (arrived|received|delivered|here)|hasn'?t (arrived|come|reached|been delivered)|didn'?t (arrive|come|reach)|never (arrived|came|reached))\b",
    r"\b(charged|deducted|debited)\b.{0,20}\b(twice|double|two times)\b",
    r"\bpayment\b.{0,15}\b(failed|deducted|debited)\b",
    r"\b(marked|shows?|showing|says?)\b.{0,12}\bdelivered\b.{0,40}\b(not|never|didn'?t)\b",
)
_REFUND_WORDS = re.compile(r"\b(refund|return|money back|exchange)\w*", re.I)
_AMOUNT = re.compile(
    r"(?:₹|\brs\.?|\binr)\s*(\d[\d,]*(?:\.\d+)?)|\b(?:of|for|worth|amount)\s+(\d[\d,]{3,})\b|(\d[\d,]*)\s*(?:rupees|rs\b|inr\b)",
    re.I,
)
_CATEGORY = {
    "damaged_wrong": "Damaged or wrong item",
    "high_value_refund": "Refunds & returns",
    "refund_issue": "Refunds & returns",
    "order_issue": "Order issue",
    "complaint": "Complaint",
    "human_request": "Speak to a person",
}
_PRECEDENCE = ["damaged_wrong", "high_value_refund", "refund_issue", "order_issue", "complaint", "human_request"]
_REASON = {
    "damaged_wrong": "Customer reports a damaged or wrong item",
    "high_value_refund": "Refund/return above the hand-off limit",
    "refund_issue": "Customer reports a refund problem",
    "order_issue": "Order-specific issue needing investigation",
    "complaint": "Customer is upset or has a complaint",
    "human_request": "Customer asked for a person",
}


def detect_escalation(text: str, refund_threshold: int = 2000) -> Escalation | None:
    tags = []
    if _DAMAGED.search(text):
        tags.append("damaged_wrong")
    if _REFUND_WORDS.search(text):
        for m in _AMOUNT.finditer(text):
            raw = next(g for g in m.groups() if g)
            try:
                if float(raw.replace(",", "")) > refund_threshold:
                    tags.append("high_value_refund")
                    break
            except ValueError:
                pass
    if _REFUND_ISSUE.search(text):
        tags.append("refund_issue")
    if _ORDER_ISSUE.search(text):
        tags.append("order_issue")
    if _COMPLAINT.search(text):
        tags.append("complaint")
    if _HUMAN.search(text):
        tags.append("human_request")
    if not tags:
        return None
    ordered = tuple(t for t in _PRECEDENCE if t in tags)
    high = any(t in ordered for t in ("damaged_wrong", "high_value_refund", "complaint"))
    return Escalation(_CATEGORY[ordered[0]], "high" if high else "normal", _REASON[ordered[0]], ordered)


_TICKET_REQUEST = _rx(
    r"\b(create|raise|open|file|submit|make|log|start)\b.{0,15}\b(a |the |my )?(support )?(ticket|complaint|request)\b",
    r"\bsupport ticket\b",
)
_YES = re.compile(r"^(yes|yeah|yep|yup|sure|ok|okay|please|please do|yes please|do it|go ahead|sounds good|that would be great|y)\W*$", re.I)
_NO = re.compile(r"^(no|nope|nah|no thanks|no thank you|not now|cancel|n)\W*$", re.I)


_ORDER_WORD = r"(?:order|oder|ordr|parcel|package|shipment|delivery|book)s?"
_ORDER_LOOKUP = _rx(
    rf"\bwhere(?:'s|s| is| are)? (?:my|the|our) {_ORDER_WORD}\b",
    rf"\bwhat(?:'s|s| is) (?:my|the) {_ORDER_WORD}\b",
    rf"\b(?:my|the)? ?{_ORDER_WORD} (?:status|update|location)\b",
    rf"\b(?:status|whereabouts|location) of (?:my|the) {_ORDER_WORD}\b",
    rf"\b(?:track|trace|check)\b.{{0,20}}\b{_ORDER_WORD}\s*(?:#|no\.?|number|id)?\s*[:#]?\s*[a-z-]*\d{{3,}}",
)


_BOOK_QUERY = _rx(
    r"\b(?:is|are|do you have|got|have you got|do you (?:sell|carry|stock))\b.{0,50}\b(?:book|title|copy|copies|available|in stock)\b",
    r"\bavailable\b.{0,20}\bbook\b|\bbook\b.{0,20}\bavailable\b",
    r"\bhow much (?:does|is|would|do)\b.{0,50}\bcost\b",
    r"\bprice (?:of|for)\b",
    r"\bwhat(?:'s| is) the price\b",
)
_BOOK_NOUN = _rx(r"\bbook\b|\btitle\b|\bcopy\b|\bcopies\b|\bnovel\b")


def is_book_query(text: str) -> bool:
    """Customer is asking about a specific book's availability/price (catalog lookup), not a live order."""
    return bool(_BOOK_QUERY.search(text))


def mentions_book_noun(text: str) -> bool:
    """A clear signal this is really about a book (vs. a generic 'how much does X cost' question),
    used to decide whether an unmatched title should still offer a ticket."""
    return bool(_BOOK_NOUN.search(text))


def is_order_lookup(text: str) -> bool:
    """Customer wants to know about THEIR specific order (needs live tracking we don't have)."""
    return bool(_ORDER_LOOKUP.search(text))


def is_ticket_request(text: str) -> bool:
    return bool(_TICKET_REQUEST.search(text))


def is_affirmative(text: str) -> bool:
    return bool(_YES.match(text.strip()))


def is_negative(text: str) -> bool:
    return bool(_NO.match(text.strip()))


# --------------------------------------------------------------------------
# Output checks (run on every LLM answer before the customer sees it)
# --------------------------------------------------------------------------
_TICKET_CLAIM = re.compile(
    r"PLB-\d{8}-\d+|\bi('ve| have|'ll have)?\s*(just )?(created|raised|opened|submitted|filed|logged)\b.{0,25}\bticket\b"
    r"|\bticket\b.{0,25}\b(has been|have been|was|is now)\b.{0,12}\b(created|raised|opened|submitted|filed|logged)\b",
    re.I,
)
_PROMISE_OUT = re.compile(r"\b(i|we) (guarantee|promise|assure you)\b", re.I)
_LEAK_HINTS = re.compile(r"\b(system prompt|my instructions are|non-negotiable rules|<faq_context>)\b", re.I)


def _numbers(text: str) -> set[str]:
    return {m.replace(",", "").lstrip("0") or "0" for m in re.findall(r"\d[\d,]*(?:\.\d+)?", text)}


def check_output(answer: str, *, context: str, question: str, secrets: list[str], canary: str) -> str | None:
    """Return a short reason if the answer must be rejected, else None."""
    if not answer or not answer.strip():
        return "empty"
    if len(answer) > 1500:
        return "too_long"
    if canary and canary in answer:
        return "prompt_leak"
    if any(s and len(s) >= 6 and s in answer for s in secrets):
        return "secret_leak"
    if _LEAK_HINTS.search(answer):
        return "prompt_leak"
    if _TICKET_CLAIM.search(answer):
        return "false_ticket_claim"
    if _PROMISE_OUT.search(answer):
        return "promise"
    if not _numbers(answer) <= (_numbers(context) | _numbers(question)):
        return "ungrounded_number"
    return None


def redact_secrets(text: str, secrets: list[str]) -> str:
    for s in secrets:
        if s and len(s) >= 6:
            text = text.replace(s, "[hidden]")
    return text


# --------------------------------------------------------------------------
# Passcode check + brute-force throttle
# --------------------------------------------------------------------------
def verify_passcode(entered: str, expected: str) -> bool:
    """Constant-time comparison. An unset passcode never matches."""
    if not expected:
        return False
    return hmac.compare_digest(entered.encode("utf-8"), expected.encode("utf-8"))


class LoginThrottle:
    """Locks logins for ``lockout`` seconds after ``max_attempts`` failures.
    Shared by all sessions in the server process."""

    def __init__(self, max_attempts: int = 5, lockout: int = 60) -> None:
        self.max_attempts, self.lockout = max_attempts, lockout
        self._fails = 0
        self._locked_until = 0.0
        self._lock = threading.Lock()

    def remaining_lock(self) -> int:
        with self._lock:
            return max(0, int(self._locked_until - time.time() + 0.999))

    def record_failure(self) -> None:
        with self._lock:
            self._fails += 1
            if self._fails >= self.max_attempts:
                self._locked_until = time.time() + self.lockout
                self._fails = 0

    def record_success(self) -> None:
        with self._lock:
            self._fails = 0
