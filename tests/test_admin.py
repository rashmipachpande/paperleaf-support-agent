"""The admin dashboard: passcode login, ticket/feedback browsing, and analytics."""
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

import feedback as feedback_store
import webapp
from ticket_system import create_ticket

PASSCODE = "letmein-654321"


@pytest.fixture()
def api(settings, monkeypatch):
    settings = replace(settings, feedback_path=settings.tickets_path.parent / "feedback.csv")
    monkeypatch.setattr(webapp, "get_settings", lambda: settings)
    for limiter in (webapp.chat_limit, webapp.ticket_limit, webapp.feedback_limit, webapp.admin_login_limit):
        limiter.clear()
    webapp._seen_tickets.clear()
    webapp._ADMIN_SESSIONS.clear()
    # https base_url: the admin cookie is now SameSite=None; Secure (for the cross-origin Vercel/Render split),
    # so it's only ever sent back over an https-looking connection.
    return TestClient(webapp.app, base_url="https://testserver"), settings


def login(client, passcode=PASSCODE):
    return client.post("/api/admin/login", json={"passcode": passcode})


def seed(settings):
    create_ticket(category="Delivery", description="Parcel never arrived", path=settings.tickets_path, priority="high")
    create_ticket(category="Delivery", description="Wrong book sent", path=settings.tickets_path, priority="normal")
    create_ticket(category="Refunds & returns", description="Refund still pending", path=settings.tickets_path, priority="normal")
    feedback_store.save_feedback(settings.feedback_path, "yes", "Q1", "A1", "faq")
    feedback_store.save_feedback(settings.feedback_path, "no", "Q2", "A2", "faq")
    feedback_store.save_feedback(settings.feedback_path, "yes", "Q3", "A3", "ticket")


# ---------------- auth ----------------
def test_wrong_passcode_is_rejected(api):
    client, _ = api
    assert login(client, "nope").status_code == 401


def test_correct_passcode_sets_a_session_cookie(api):
    client, _ = api
    r = login(client)
    assert r.status_code == 200 and r.json()["ok"]
    assert client.cookies.get("admin_session")


def test_data_routes_require_login(api):
    client, _ = api
    assert client.get("/api/admin/tickets").status_code == 401
    assert client.get("/api/admin/feedback").status_code == 401
    assert client.get("/api/admin/analytics").status_code == 401
    assert client.get("/api/admin/me").status_code == 401
    login(client)
    assert client.get("/api/admin/tickets").status_code == 200
    assert client.get("/api/admin/me").status_code == 200


def test_logout_invalidates_the_session(api):
    client, _ = api
    login(client)
    assert client.get("/api/admin/me").status_code == 200
    assert client.post("/api/admin/logout").status_code == 200
    assert client.get("/api/admin/me").status_code == 401


def test_login_is_rate_limited(api):
    client, _ = api
    codes = [login(client, "nope").status_code for _ in range(6)]
    assert codes[:5] == [401] * 5 and codes[5] == 429


def test_login_fails_cleanly_when_passcode_is_not_configured(api, monkeypatch):
    client, settings = api
    monkeypatch.setattr(webapp, "get_settings", lambda: replace(settings, app_passcode=""))
    assert login(client).status_code == 503


# ---------------- data ----------------
def test_tickets_are_listed_and_filtered(api):
    client, settings = api
    seed(settings)
    login(client)
    all_rows = client.get("/api/admin/tickets").json()
    assert len(all_rows) == 3
    by_priority = client.get("/api/admin/tickets", params={"priority": "high"}).json()
    assert len(by_priority) == 1 and by_priority[0]["description"] == "Parcel never arrived"
    by_category = client.get("/api/admin/tickets", params={"category": "Delivery"}).json()
    assert len(by_category) == 2
    by_search = client.get("/api/admin/tickets", params={"q": "refund"}).json()
    assert len(by_search) == 1 and by_search[0]["category"] == "Refunds & returns"


def test_feedback_is_listed_and_filtered(api):
    client, settings = api
    seed(settings)
    login(client)
    assert len(client.get("/api/admin/feedback").json()) == 3
    yes_only = client.get("/api/admin/feedback", params={"rating": "yes"}).json()
    assert len(yes_only) == 2
    by_intent = client.get("/api/admin/feedback", params={"intent": "ticket"}).json()
    assert len(by_intent) == 1 and by_intent[0]["question"] == "Q3"


def test_empty_data_returns_empty_lists_not_errors(api):
    client, _ = api
    login(client)
    assert client.get("/api/admin/tickets").json() == []
    assert client.get("/api/admin/feedback").json() == []


# ---------------- analytics ----------------
def test_analytics_summarises_tickets_and_feedback(api):
    client, settings = api
    seed(settings)
    login(client)
    data = client.get("/api/admin/analytics").json()
    assert data["tickets"]["by_priority"] == {"high": 1, "normal": 2}
    assert data["tickets"]["by_category"]["Delivery"] == 2
    assert data["feedback"]["yes"] == 2 and data["feedback"]["no"] == 1
    assert data["feedback"]["yes_rate"] == pytest.approx(2 / 3, rel=1e-3)
    assert len(data["trend"]) == 30
    assert sum(p["tickets"] for p in data["trend"]) == 3
    assert sum(p["feedback"] for p in data["trend"]) == 3


def test_analytics_on_empty_data_has_no_division_errors(api):
    client, _ = api
    login(client)
    data = client.get("/api/admin/analytics").json()
    assert data["feedback"]["yes_rate"] == 0.0
    assert all(p["tickets"] == 0 and p["feedback"] == 0 for p in data["trend"])


# ---------------- security ----------------
# The /admin page itself is served by the frontend deployment now (e.g. Vercel), not this
# API-only backend — only confirm the data routes stay gated without a session.
def test_data_stays_gated_without_a_session(api):
    client, _ = api
    assert client.get("/api/admin/tickets").status_code == 401


def test_cross_site_admin_login_is_blocked(api):
    client, _ = api
    r = client.post("/api/admin/login", json={"passcode": PASSCODE}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
