"""Unit tests: knowledge base loading, ticket storage, login throttle."""
import csv
import time

import pytest
import requests

import guardrails as gr
import knowledge_base as kbm
import ticket_system as ts

CSV = "Category,Question,Answer\nDelivery,How long does delivery take?,2 to 4 days.\n"


def test_parse_ok_and_columns_case_insensitive():
    faqs = kbm.parse_faq_csv(" Category ,QUESTION,answer\nA,Q?,Ans\n,,\n")
    assert len(faqs) == 1 and faqs[0].category == "A"


@pytest.mark.parametrize("bad", ["<html><body>Sign in</body></html>", "Foo,Bar\n1,2\n", "Category,Question,Answer\n", ""])
def test_invalid_csv_raises(bad):
    with pytest.raises(kbm.KnowledgeBaseError):
        kbm.parse_faq_csv(bad)


def test_sheet_url_normalised():
    url = kbm.normalize_csv_url("https://docs.google.com/spreadsheets/d/abc123/edit#gid=5")
    assert url == "https://docs.google.com/spreadsheets/d/abc123/export?format=csv&gid=5"


def test_loader_caches_then_falls_back(monkeypatch, tmp_path):
    kbm._LOADERS.clear()
    calls = {"n": 0, "fail": False}

    def fake_fetch(url, timeout):
        calls["n"] += 1
        if calls["fail"]:
            raise kbm.KnowledgeBaseError("down")
        return CSV

    monkeypatch.setattr(kbm, "_fetch", fake_fetch)
    cache = tmp_path / "c.csv"
    kb = kbm.get_knowledge_base("http://x/a", cache_path=cache, ttl=300)
    assert kb.status.state == "ok" and len(kb) == 1 and cache.exists()
    kbm.get_knowledge_base("http://x/a", cache_path=cache, ttl=300)
    assert calls["n"] == 1  # served from memory
    calls["fail"] = True
    assert kbm.get_knowledge_base("http://x/a", cache_path=cache, force=True).status.state == "stale"
    kbm._LOADERS.clear()  # fresh process: only the disk copy is left
    assert kbm.get_knowledge_base("http://x/a", cache_path=cache).status.state == "cached"
    kbm._LOADERS.clear()
    cache.unlink()
    unavailable = kbm.get_knowledge_base("http://x/a", cache_path=cache)
    assert unavailable.status.state == "unavailable" and len(unavailable) == 0


def test_network_error_becomes_kb_error(monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("no net")

    monkeypatch.setattr(kbm.requests, "get", boom)
    with pytest.raises(kbm.KnowledgeBaseError):
        kbm._fetch("http://x", 1)


def test_ticket_ids_sequence_and_csv(tmp_path):
    path = tmp_path / "t.csv"
    a = ts.create_ticket(category="Delivery", description="Where is it?", path=path)
    b = ts.create_ticket(category="Bogus", description="=HYPERLINK(evil)", path=path, priority="high")
    assert a.ticket_id.startswith("PLB-") and a.ticket_id.endswith("-001") and b.ticket_id.endswith("-002")
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
    assert rows[1]["category"] == "General question" and rows[1]["description"].startswith("'=")
    assert rows[1]["priority"] == "high" and rows[0]["status"] == "open"


def test_ticket_validation(tmp_path):
    with pytest.raises(ts.TicketError):
        ts.create_ticket(category="Delivery", description="hi", path=tmp_path / "t.csv")
    with pytest.raises(ts.TicketError):
        ts.create_ticket(category="Delivery", description="long enough text", customer_email="nope", path=tmp_path / "t.csv")
    assert not (tmp_path / "t.csv").exists()


def test_passcode_and_throttle():
    assert gr.verify_passcode("abc", "abc") and not gr.verify_passcode("abd", "abc")
    assert not gr.verify_passcode("", "")  # unset passcode never matches
    t = gr.LoginThrottle(max_attempts=2, lockout=1)
    t.record_failure(); t.record_failure()
    assert t.remaining_lock() > 0
    time.sleep(1.1)
    assert t.remaining_lock() == 0
