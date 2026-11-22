"""PaperLeaf Books – Paige support assistant (Streamlit UI).

Run with:  streamlit run app.py
"""
from __future__ import annotations

import logging
import time
import uuid

import streamlit as st

import guardrails as gr
from agent import GREETING, AgentReply, PaigeAgent, TicketOffer
from config import AGENT_NAME, APP_NAME, LOGO_PATH, PAGE_TITLE, TICKET_CATEGORIES, get_settings
from knowledge_base import get_knowledge_base

logging.basicConfig(level=logging.INFO)

st.set_page_config(
    page_title=PAGE_TITLE,
    page_icon=str(LOGO_PATH) if LOGO_PATH.exists() else "📚",
    layout="centered",
    initial_sidebar_state="expanded",
)

PAIGE_AVATAR = "📖"
USER_AVATAR = "🧑‍🎓"
SUGGESTIONS = [
    "How long does delivery take?",
    "What are your shipping charges?",
    "What payment methods do you accept?",
    "How do I return a book?",
    "How long do refunds take?",
    "Can I pre-order a book?",
    "Do you offer gift wrapping?",
]

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Lora:wght@400;500;600&family=Playfair+Display:wght@600;700&display=swap');
:root { --cream:#FBF6EC; --beige:#F1E7D3; --card:#FFFDF8; --line:#E6D9BF; --green:#2F5D44; --green-d:#234735;
        --brown:#8B6B4A; --ink:#3B2A1E; }
html, body, [class*="css"], .stApp, .stMarkdown, p, li, label, input, textarea { font-family:'Lora', Georgia, serif; }
h1, h2, h3, .brand { font-family:'Playfair Display', Georgia, serif !important; color:var(--green-d); letter-spacing:.2px; }
.stApp { background: radial-gradient(circle at 15% 0%, #FFF9EE 0%, var(--cream) 55%); color:var(--ink); }
header[data-testid="stHeader"] { background:transparent; }
.block-container { max-width: 820px; padding-top: 2rem; padding-bottom: 6rem; }

/* sidebar */
section[data-testid="stSidebar"] { background: var(--beige); border-right:1px solid var(--line); }
.side-card { background:var(--card); border:1px solid var(--line); border-radius:16px; padding:.9rem 1rem;
             box-shadow:0 2px 8px rgba(91,64,36,.07); margin-bottom:.8rem; font-size:.92rem; line-height:1.45; }
.side-card h4 { margin:0 0 .35rem 0; font-family:'Playfair Display',serif; color:var(--green-d); font-size:1rem; }
.side-card ul { margin:.2rem 0 0 1.1rem; padding:0; }
.brand { font-size:1.55rem; font-weight:700; margin:.2rem 0 0 0; line-height:1.1; }
.tagline { color:var(--brown); font-size:.85rem; margin-bottom:.8rem; font-style:italic; }
.pill { display:inline-block; padding:.12rem .6rem; border-radius:999px; font-size:.78rem; font-weight:600; }
.pill.ok { background:#E3F0E7; color:var(--green-d); } .pill.warn { background:#FBEBC8; color:#8A5A00; }
.pill.bad { background:#F8DDD8; color:#9B2C1F; }

/* hero */
.hero { background:linear-gradient(135deg,var(--green) 0%, #3E7556 100%); color:#FBF6EC; border-radius:22px;
        padding:1.3rem 1.6rem; box-shadow:0 8px 24px rgba(47,93,68,.22); margin-bottom:1.1rem; }
.hero h1 { color:#FBF6EC !important; margin:0; font-size:1.75rem; }
.hero p { margin:.25rem 0 0 0; color:#E4EDE4; font-size:.98rem; }

/* chat */
div[data-testid="stChatMessage"] { background:var(--card); border:1px solid var(--line); border-radius:18px;
        padding:.8rem 1rem; box-shadow:0 2px 8px rgba(91,64,36,.06); margin-bottom:.6rem; }
div[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) { background:#EFE4CC; }
div[data-testid="stChatInput"] { border-radius:16px; border:1px solid var(--line); background:var(--card); }
div[data-testid="stChatInput"] textarea { font-family:'Lora',serif; }

/* buttons */
.stButton > button, .stFormSubmitButton > button { border-radius:999px; border:1px solid var(--green); color:var(--green-d);
        background:var(--card); font-family:'Lora',serif; transition:all .15s ease; }
.stButton > button:hover, .stFormSubmitButton > button:hover { background:var(--green); color:#FBF6EC; border-color:var(--green); }
.stFormSubmitButton > button[kind="primary"], .stFormSubmitButton:first-child > button { background:var(--green); color:#FBF6EC; }
.st-key-suggestions .stButton > button { width:100%; text-align:left; border-radius:14px; padding:.55rem .9rem;
        border:1px solid var(--line); box-shadow:0 1px 4px rgba(91,64,36,.07); }
.st-key-suggestions .stButton > button:hover { background:var(--beige); color:var(--green-d); border-color:var(--green); }
.ticket-card { background:#F7F0E0; border:1px dashed var(--brown); border-radius:14px; padding:.6rem .9rem; margin:.5rem 0 .2rem 0;
        color:var(--brown); font-size:.9rem; }
.login-card { background:var(--card); border:1px solid var(--line); border-radius:22px; padding:2rem 1.6rem 1.4rem;
        box-shadow:0 10px 30px rgba(91,64,36,.12); text-align:center; margin-top:1rem; }
.login-card h1 { margin:.4rem 0 0 0; }
.login-card p { color:var(--brown); }
#MainMenu, footer { visibility:hidden; }
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


# --------------------------------------------------------------------------
# Login
# --------------------------------------------------------------------------
@st.cache_resource
def get_throttle() -> gr.LoginThrottle:
    return gr.LoginThrottle(max_attempts=5, lockout=60)


def render_login(passcode: str) -> None:
    _, mid, _ = st.columns([1, 6, 1])
    with mid:
        if LOGO_PATH.exists():
            left, centre, right = st.columns([2, 1, 2])
            centre.image(str(LOGO_PATH), use_container_width=True)
        st.markdown(
            '<div class="login-card" style="margin-top:0"><h1>PaperLeaf Books</h1>'
            f"<p>Customer support with {AGENT_NAME}. Please enter the passcode to continue.</p></div>",
            unsafe_allow_html=True,
        )
        if not passcode:
            st.error("This app isn't configured yet: no passcode has been set, so it stays locked.")
            return
        throttle = get_throttle()
        locked = throttle.remaining_lock()
        with st.form("login"):
            entered = st.text_input("Passcode", type="password", placeholder="Enter passcode", disabled=bool(locked))
            submitted = st.form_submit_button("Unlock chat", use_container_width=True, disabled=bool(locked))
        if locked:
            st.warning(f"Too many attempts. Please wait {locked} seconds and try again.")
        elif submitted:
            if gr.verify_passcode(entered, passcode):
                throttle.record_success()
                st.session_state.authenticated = True
                st.rerun()
            else:
                throttle.record_failure()
                st.error("That passcode doesn't look right. Please try again.")


# --------------------------------------------------------------------------
# State helpers
# --------------------------------------------------------------------------
def init_state() -> None:
    st.session_state.setdefault("messages", [new_message("assistant", GREETING, kind="welcome")])
    st.session_state.setdefault("my_tickets", [])
    st.session_state.setdefault("ticket_errors", {})
    st.session_state.setdefault("ticket_keys", {})


def new_message(role: str, content: str, **extra) -> dict:
    return {"id": uuid.uuid4().hex[:8], "role": role, "content": content, **extra}


def reply_to_message(reply: AgentReply) -> dict:
    offer = reply.offer.__dict__ if reply.offer else None
    return new_message("assistant", reply.text, intent=reply.intent, offer=offer, notice=reply.notice)


def clear_chat() -> None:
    st.session_state.messages = [new_message("assistant", GREETING, kind="welcome")]
    st.session_state.ticket_errors = {}


def queue_prompt(text: str) -> None:
    st.session_state.queued_prompt = text


def logout() -> None:
    for key in list(st.session_state.keys()):
        del st.session_state[key]


def llm_history() -> list[dict]:
    return [
        {"role": m["role"], "content": m["content"], "intent": m.get("intent")}
        for m in st.session_state.messages
        if m.get("kind") != "welcome"
    ]


# --------------------------------------------------------------------------
# Ticket form
# --------------------------------------------------------------------------
def submit_ticket(msg_id: str) -> None:
    """Form callback: runs before the page reruns, so the result shows up in the chat."""
    msg = next(m for m in st.session_state.messages if m["id"] == msg_id)
    offer = TicketOffer(**msg["offer"])
    offer.category = st.session_state[f"cat_{msg_id}"]
    agent = make_agent()
    result = agent.submit_ticket(
        offer,
        name=st.session_state[f"name_{msg_id}"],
        email=st.session_state[f"email_{msg_id}"],
        description=st.session_state[f"desc_{msg_id}"],
        seen=st.session_state.ticket_keys,
    )
    if result.ticket_id:
        msg["offer"] = None
        st.session_state.ticket_errors.pop(msg_id, None)
        if result.ticket_id not in st.session_state.my_tickets:
            st.session_state.my_tickets.append(result.ticket_id)
        st.session_state.messages.append(new_message("assistant", result.text, intent="ticket"))
    else:
        st.session_state.ticket_errors[msg_id] = result.text


def dismiss_ticket(msg_id: str) -> None:
    msg = next(m for m in st.session_state.messages if m["id"] == msg_id)
    msg["offer"] = None
    st.session_state.messages.append(
        new_message("assistant", "No problem at all! 😊 I'm here if you change your mind or have another question.", intent="smalltalk")
    )


def render_ticket_form(msg: dict) -> None:
    mid, offer = msg["id"], msg["offer"]
    st.markdown('<div class="ticket-card">🎫 <b>Support ticket</b> – check the details, then create it.</div>', unsafe_allow_html=True)
    if mid in st.session_state.ticket_errors:
        st.error(st.session_state.ticket_errors[mid])
    category = offer["category"] if offer["category"] in TICKET_CATEGORIES else TICKET_CATEGORIES[0]
    with st.form(f"ticket_{mid}", border=False):
        c1, c2 = st.columns(2)
        c1.text_input("Your name (optional)", key=f"name_{mid}", max_chars=80)
        c2.text_input("Email (optional)", key=f"email_{mid}", max_chars=120)
        st.selectbox("Issue category", TICKET_CATEGORIES, index=TICKET_CATEGORIES.index(category), key=f"cat_{mid}")
        st.text_area("What happened?", value=offer["description"], key=f"desc_{mid}", max_chars=1500, height=100)
        b1, b2 = st.columns([2, 1])
        b1.form_submit_button("🎫 Create ticket", on_click=submit_ticket, args=(mid,), use_container_width=True)
        b2.form_submit_button("No thanks", on_click=dismiss_ticket, args=(mid,), use_container_width=True)


# --------------------------------------------------------------------------
# Sidebar
# --------------------------------------------------------------------------
def render_sidebar(kb, settings) -> None:
    with st.sidebar:
        if LOGO_PATH.exists():
            st.image(str(LOGO_PATH), width=72)
        st.markdown(f'<div class="brand">{APP_NAME}</div><div class="tagline">Stories delivered to your door</div>', unsafe_allow_html=True)
        st.markdown(
            f'<div class="side-card"><h4>Meet {AGENT_NAME} 📖</h4>Your friendly bookish assistant. '
            "She answers questions from our help pages and passes anything tricky to our support team.</div>",
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div class="side-card"><h4>I can help with</h4><ul><li>Delivery &amp; shipping</li><li>Payments</li>'
            "<li>Returns &amp; exchanges</li><li>Refunds</li><li>Pre-orders</li><li>Gift wrapping</li></ul></div>",
            unsafe_allow_html=True,
        )
        status = kb.status
        pill = {"ok": ("ok", "Connected"), "stale": ("warn", "Using last copy"), "cached": ("warn", "Using saved copy")}.get(
            status.state, ("bad", "Unavailable")
        )
        updated = f" · updated {status.loaded_at:%I:%M %p}" if status.loaded_at else ""
        llm = "AI replies on" if settings.llm_configured else "FAQ-only mode (no API key)"
        st.markdown(
            f'<div class="side-card"><h4>Knowledge base</h4><span class="pill {pill[0]}">{pill[1]}</span> '
            f"{status.faq_count} FAQs{updated}<br><small>{status.message}<br>{llm}</small></div>",
            unsafe_allow_html=True,
        )
        if st.button("🔄 Refresh FAQs", use_container_width=True):
            get_knowledge_base(settings.faq_csv_url, cache_path=settings.faq_cache_path, ttl=settings.faq_cache_ttl, timeout=settings.faq_timeout, force=True)
            st.rerun()
        if st.button("🧹 Clear chat", use_container_width=True):
            clear_chat()
            st.rerun()

        support = kb.by_category("Support")
        info = "".join(f"<li>{f.answer}</li>" for f in support) or "<li>Ask Paige to create a support ticket.</li>"
        st.markdown(f'<div class="side-card"><h4>Support</h4><ul>{info}</ul>'
                    "<small>Need a person? Just say “talk to a human”.</small></div>", unsafe_allow_html=True)
        if st.session_state.my_tickets:
            items = "".join(f"<li><code>{t}</code></li>" for t in st.session_state.my_tickets)
            st.markdown(f'<div class="side-card"><h4>Your tickets 🎫</h4><ul>{items}</ul></div>', unsafe_allow_html=True)
        st.button("🔒 Log out", on_click=logout, use_container_width=True)


# --------------------------------------------------------------------------
# Main chat
# --------------------------------------------------------------------------
def make_agent() -> PaigeAgent:
    settings = get_settings()
    kb = get_knowledge_base(settings.faq_csv_url, cache_path=settings.faq_cache_path, ttl=settings.faq_cache_ttl, timeout=settings.faq_timeout)
    return PaigeAgent(kb, settings)


def stream_text(text: str):
    for word in text.split(" "):
        yield word + " "
        time.sleep(0.015)


def render_message(msg: dict, is_last: bool) -> None:
    avatar = PAIGE_AVATAR if msg["role"] == "assistant" else USER_AVATAR
    with st.chat_message(msg["role"], avatar=avatar):
        st.markdown(msg["content"])
        if msg.get("notice"):
            st.caption(f"ℹ️ {msg['notice']}")
        if msg.get("offer") and is_last:
            render_ticket_form(msg)


def main() -> None:
    settings = get_settings()
    if not st.session_state.get("authenticated"):
        render_login(settings.app_passcode)
        return

    init_state()
    agent = make_agent()
    render_sidebar(agent.kb, settings)

    st.markdown(
        f'<div class="hero"><h1>Chat with {AGENT_NAME}</h1><p>Questions about delivery, payments, returns, '
        "refunds, pre-orders or gift wrapping? Ask away.</p></div>",
        unsafe_allow_html=True,
    )

    messages = st.session_state.messages
    for i, msg in enumerate(messages):
        render_message(msg, is_last=i == len(messages) - 1)

    if len(messages) <= 1:
        st.markdown("**💡 Try asking**")
        with st.container(key="suggestions"):
            cols = st.columns(2)
            for i, q in enumerate(SUGGESTIONS):
                cols[i % 2].button(q, key=f"sg_{i}", on_click=queue_prompt, args=(q,))
    else:
        with st.expander("💡 Suggested questions"):
            with st.container(key="suggestions"):
                for i, q in enumerate(SUGGESTIONS):
                    st.button(q, key=f"sg_{i}", on_click=queue_prompt, args=(q,))

    typed = st.chat_input(f"Ask {AGENT_NAME} about delivery, returns, refunds…", max_chars=settings.max_message_chars + 500)
    prompt = st.session_state.pop("queued_prompt", None) or typed
    if prompt is None:
        return

    shown = gr.redact_sensitive(gr.normalize_input(prompt)) or "…"
    history = llm_history()
    user_msg = new_message("user", shown, intent=None)
    with st.chat_message("user", avatar=USER_AVATAR):
        st.markdown(shown)
    with st.chat_message("assistant", avatar=PAIGE_AVATAR):
        with st.spinner(f"{AGENT_NAME} is flipping through the pages…"):
            reply = agent.respond(prompt, history)
        st.write_stream(stream_text(reply.text))
        if reply.notice:
            st.caption(f"ℹ️ {reply.notice}")
    user_msg["intent"] = reply.intent
    messages.append(user_msg)
    messages.append(reply_to_message(reply))
    if reply.offer:
        st.rerun()  # redraw so the ticket form appears under the newest message


try:
    main()
except Exception:  # last line of defence: never show a stack trace to customers
    logging.exception("Unhandled error in app")
    st.error("Something went wrong on our side. Please refresh the page and try again. 📚")
