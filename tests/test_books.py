"""Books mode logic that doesn't need Screen Recording: reading order, page
comparison, chunking, and the page-turn endpoint with OCR mocked out."""
import pytest
from fastapi.testclient import TestClient

import app as app_module
import books_reader
from books_reader import lines_to_chunks, same_page, sort_reading_order


def line(text, x, top, w=0.35, h=0.03):
    """A Vision-style line: bbox = (x, bottom_y, width, height), normalised."""
    return {"text": text, "bbox": (x, top - h, w, h)}


# ── Reading order ────────────────────────────────────────────────────────────

def test_two_page_spread_reads_left_page_then_right_page():
    lines = []
    for i in range(4):
        lines.append(line(f"R{i}", 0.55, 0.9 - i * 0.05 + 0.001))
        lines.append(line(f"L{i}", 0.08, 0.9 - i * 0.05))
    order = [l["text"] for l in sort_reading_order(lines)]
    assert order == ["L0", "L1", "L2", "L3", "R0", "R1", "R2", "R3"]


def test_spread_running_title_comes_first():
    lines = [line(f"L{i}", 0.08, 0.8 - i * 0.05) for i in range(3)]
    lines += [line(f"R{i}", 0.55, 0.8 - i * 0.05) for i in range(3)]
    lines.append(line("Book Title", 0.42, 0.95, w=0.16))
    order = [l["text"] for l in sort_reading_order(lines)]
    assert order[0] == "Book Title"
    assert order[1:4] == ["L0", "L1", "L2"]


def test_single_page_is_plain_top_to_bottom():
    # Full-width lines cross the middle of the window; a short last line of a
    # paragraph must not trigger spread mode.
    lines = [line(f"P{i}", 0.15, 0.9 - i * 0.05, w=0.7) for i in range(8)]
    lines.append(line("Yes.", 0.15, 0.4, w=0.06))
    order = [l["text"] for l in sort_reading_order(list(reversed(lines)))]
    assert order == [f"P{i}" for i in range(8)] + ["Yes."]


# ── Page comparison and chunking ─────────────────────────────────────────────

def test_same_page_tolerates_small_ocr_noise():
    a = "It was the best of times, it was the worst of times, it was the age of wisdom."
    assert same_page(a, a)
    assert same_page(a, a.replace("wisdom", "wisdon"))
    assert not same_page(a, "A completely different page of text about other things.")
    assert not same_page("", a)


def test_short_sentences_survive_chunking():
    assert [c["text"] for c in lines_to_chunks([{"text": "Yes.", "bbox": (0, 0, 0, 0)}])] == ["Yes."]
    assert lines_to_chunks([{"text": "—", "bbox": (0, 0, 0, 0)}]) == []


# ── /books/next_page with OCR mocked ─────────────────────────────────────────

PAGE_1 = [line("Chapter One", 0.3, 0.95, w=0.4)] + [
    line(f"First page sentence number {i}.", 0.15, 0.85 - i * 0.05, w=0.7) for i in range(5)]
PAGE_2 = [line(f"Second page has other words, line {i}.", 0.15, 0.9 - i * 0.05, w=0.7)
          for i in range(5)]
WIN = {"id": 42, "x": 0, "y": 0, "width": 800, "height": 600}


@pytest.fixture
def books(monkeypatch):
    monkeypatch.setattr(app_module, "_PAGE_POLL_INTERVAL", 0)
    monkeypatch.setattr(app_module, "_PAGE_POLL_ATTEMPTS", 4)
    state = {"page": PAGE_1, "turns": 0}

    def fake_ocr():
        return list(state["page"]), dict(WIN)

    monkeypatch.setattr(books_reader, "ocr_page", fake_ocr)
    with TestClient(app_module.app) as c:
        yield c, state


def test_capture_then_next_page_returns_new_page(books, monkeypatch):
    client, state = books

    def turn():
        state["turns"] += 1
        state["page"] = PAGE_2

    monkeypatch.setattr(books_reader, "turn_page", turn)
    first = client.post("/books/capture").json()
    assert first["page_title"] == "Chapter One"
    r = client.post("/books/next_page")
    assert r.status_code == 200
    assert "Second page" in r.json()["chunks"][0]["text"]
    assert state["turns"] == 1


def test_next_page_stops_when_page_does_not_change(books, monkeypatch):
    client, state = books
    monkeypatch.setattr(books_reader, "turn_page", lambda: state.update(turns=state["turns"] + 1))
    client.post("/books/capture")
    r = client.post("/books/next_page")
    assert r.status_code == 409
    assert state["turns"] == 1


def test_page_turn_permission_error_is_reported(books, monkeypatch):
    client, _ = books

    def blocked():
        raise books_reader.PageTurnError("macOS blocked the page-turn key press.")

    monkeypatch.setattr(books_reader, "turn_page", blocked)
    client.post("/books/capture")
    r = client.post("/books/next_page")
    assert r.status_code == 503
    assert "blocked" in r.json()["detail"]


def test_clear_does_not_turn_the_page(books, monkeypatch):
    client, state = books
    monkeypatch.setattr(books_reader, "turn_page", lambda: state.update(turns=state["turns"] + 1))
    assert client.post("/books/clear").json() == {"ok": True}
    assert state["turns"] == 0
