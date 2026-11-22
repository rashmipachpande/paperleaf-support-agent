"""Sample book catalog: a small local CSV of titles, so Paige can answer
'is this book available?' questions directly instead of offering a ticket."""
from __future__ import annotations

import csv
import difflib
import re
from dataclasses import dataclass
from pathlib import Path

# Words that frame a lookup but aren't part of the title itself, e.g.
# "do you have Atomic Habits in stock" -> "atomic habits".
_WRAPPER = re.compile(
    r"\b(do you (?:have|sell|carry|stock)|is|are|got|have you got|how much (?:does|is|would|do)|"
    r"what('?s| is) the price (?:of|for)|price (?:of|for)|cost of|available|in stock|a copy of|copies of|"
    r"the book|book called|titled|please|thanks?|thank you)\b",
    re.IGNORECASE,
)
_PUNCT = re.compile(r"[^\w\s]")


@dataclass(frozen=True)
class Book:
    title: str
    author: str
    price: float
    in_stock: bool
    description: str


def _clean(text: str) -> str:
    text = _WRAPPER.sub(" ", text)
    text = _PUNCT.sub(" ", text)
    return " ".join(text.lower().split())


class BookCatalog:
    def __init__(self, path: Path) -> None:
        self._books: list[Book] = self._load(path)

    @staticmethod
    def _load(path: Path) -> list[Book]:
        if not path.exists():
            return []
        with path.open("r", newline="", encoding="utf-8-sig") as fh:
            books = []
            for row in csv.DictReader(fh):
                try:
                    books.append(Book(
                        title=row["title"].strip(),
                        author=row["author"].strip(),
                        price=float(row["price"]),
                        in_stock=row["in_stock"].strip().lower() == "yes",
                        description=row["description"].strip(),
                    ))
                except (KeyError, ValueError):
                    continue
            return books

    def find(self, query: str) -> Book | None:
        """Exact (case-insensitive) match -> substring match -> fuzzy match on title. None if nothing close."""
        if not self._books:
            return None
        needle = _clean(query)
        if not needle:
            return None

        for book in self._books:
            if _clean(book.title) == needle:
                return book

        contains = [book for book in self._books if needle in _clean(book.title) or _clean(book.title) in needle]
        if len(contains) == 1:
            return contains[0]
        if len(contains) > 1:  # ambiguous substring match: prefer the shortest (most specific) title
            return min(contains, key=lambda b: len(b.title))

        titles = {_clean(book.title): book for book in self._books}
        match = difflib.get_close_matches(needle, titles.keys(), n=1, cutoff=0.6)
        return titles[match[0]] if match else None
