"""Paige answering 'is this book available?' style questions from the local book catalog."""


def test_exact_title_in_stock(agent):
    reply = agent.respond("Is Atomic Habits available?")
    assert reply.intent == "book_lookup"
    assert "Atomic Habits" in reply.text and "James Clear" in reply.text
    assert "In stock" in reply.text and "499.00" in reply.text
    assert reply.offer is None and reply.sources == ["Book catalog"]


def test_exact_title_out_of_stock(agent):
    reply = agent.respond("Do you have the book 1984?")
    assert reply.intent == "book_lookup"
    assert "1984" in reply.text and "out of stock" in reply.text.lower()


def test_fuzzy_typo_title_still_resolves(agent):
    reply = agent.respond("Is Atomic Habbits available?")
    assert reply.intent == "book_lookup" and "Atomic Habits" in reply.text


def test_partial_title_resolves(agent):
    reply = agent.respond("Do you have the Alchemist in stock?")
    assert reply.intent == "book_lookup" and "The Alchemist" in reply.text


def test_unknown_title_offers_a_ticket(agent):
    reply = agent.respond("Is the book Harry Potter and the Chamber of Secrets available?")
    assert reply.intent == "book_lookup"
    assert "couldn't find" in reply.text.lower()
    assert reply.offer is not None and reply.offer.category == "General question"


def test_generic_cost_question_without_a_title_is_not_hijacked(agent):
    """'How much does delivery cost?' must still be answered from the FAQ, not treated as an unmatched book."""
    reply = agent.respond("How much does delivery cost?")
    assert reply.intent == "faq" and "₹49" in reply.text


def test_ordinary_faq_question_is_unaffected(agent):
    reply = agent.respond("What is your return policy?")
    assert reply.intent == "faq"
