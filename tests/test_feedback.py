import csv

import feedback


def read(path):
    return list(csv.DictReader(path.open(encoding="utf-8-sig")))


def test_saves_rows_with_header(tmp_path):
    path = tmp_path / "feedback.csv"
    assert feedback.save_feedback(path, "yes", "How much is delivery?", "Free above ₹499.", "faq")
    assert feedback.save_feedback(path, "no", "Where is my order?", "…", "escalation")
    rows = read(path)
    assert [r["rating"] for r in rows] == ["yes", "no"]
    assert rows[0]["question"] == "How much is delivery?" and rows[0]["intent"] == "faq"
    assert len(rows[0]["timestamp"]) == 19


def test_rejects_unknown_rating(tmp_path):
    assert not feedback.save_feedback(tmp_path / "f.csv", "maybe", "q", "r")
    assert not (tmp_path / "f.csv").exists()


def test_formula_injection_is_neutralised(tmp_path):
    path = tmp_path / "f.csv"
    feedback.save_feedback(path, "no", "=HYPERLINK(\"http://evil\")", "+cmd", "faq")
    row = read(path)[0]
    assert row["question"].startswith("'=") and row["reply"].startswith("'+")


def test_write_failure_returns_false_and_does_not_raise(tmp_path):
    (tmp_path / "dir").mkdir()
    assert feedback.save_feedback(tmp_path / "dir", "yes", "q", "r") is False
