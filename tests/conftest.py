import sys
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import LLMError, PaigeAgent  # noqa: E402
from book_catalog import BookCatalog  # noqa: E402
from config import Settings  # noqa: E402
from knowledge_base import KnowledgeBase  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "faq_sample.csv"
BOOK_FIXTURE = Path(__file__).parent / "fixtures" / "book_sample.csv"


@pytest.fixture(scope="session")
def kb():
    return KnowledgeBase.from_csv_text(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def catalog():
    return BookCatalog(BOOK_FIXTURE)


@pytest.fixture
def settings(tmp_path):
    return Settings(google_api_key="test-key-123456", app_passcode="letmein-654321", tickets_path=tmp_path / "tickets.csv")


class EchoLLM:
    """Pretends to be Gemini: rewords the first FAQ answer it was given."""

    def generate(self, system_prompt, history, user_message):
        answer = system_prompt.split("Answer: ", 1)[1].split("\n", 1)[0]
        return f"Happy to help! {answer}"


class ScriptedLLM:
    def __init__(self, reply=None, error=None):
        self.reply, self.error = reply, error

    def generate(self, system_prompt, history, user_message):
        if self.error:
            raise LLMError(self.error)
        return self.reply


@pytest.fixture
def agent(kb, settings, catalog):
    return PaigeAgent(kb, settings, llm=EchoLLM(), catalog=catalog)


@pytest.fixture
def make_agent(kb, settings, catalog):
    def _make(llm=None, **overrides):
        return PaigeAgent(kb, replace(settings, **overrides), llm=llm, catalog=catalog)

    return _make
