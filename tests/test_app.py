import json

import pymupdf as fitz
import pytest
from fastapi.testclient import TestClient

import app as app_module


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(app_module, "HIST_FILE", tmp_path / "history.json")
    monkeypatch.setattr(app_module, "DOCS_DIR", tmp_path / "docs")
    # Nothing listens on the discard port: TTS calls fail fast with 503.
    monkeypatch.setattr(app_module, "TTS_URL", "http://127.0.0.1:9")
    app_module.documents.clear()
    return tmp_path


@pytest.fixture
def client(home):
    with TestClient(app_module.app) as c:
        yield c


def _pdf_bytes(text=None):
    doc = fitz.open()
    page = doc.new_page()
    if text:
        page.insert_textbox(fitz.Rect(50, 50, 550, 800), text, fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


def _upload(client, text="A first sentence that is long enough. And a second one here."):
    r = client.post("/upload", files={"file": ("paper.pdf", _pdf_bytes(text), "application/pdf")})
    return r


def _history_payload(doc_id, chunks=("Hello there.",)):
    return {
        "doc_id": doc_id, "filename": "x.pdf", "timestamp": "2026-05-06T20:59:17.944Z",
        "total_chunks": len(chunks),
        "data": {"doc_id": doc_id, "sections": [{"name": "Document", "chunk_idx": 0}],
                 "chunks": [{"text": t, "section": "Document", "index": i} for i, t in enumerate(chunks)],
                 "total_chunks": len(chunks)},
    }


# ── Document ids ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", ["../../escaped", "..", "a/b", "", ".hidden", "x" * 70, "a b"])
def test_history_rejects_unsafe_ids(client, home, bad):
    r = client.post("/history", json=_history_payload(bad))
    assert r.status_code in (400, 422)
    assert not list(home.parent.glob("escaped*.json"))
    assert not (home / "escaped.json").exists()


@pytest.mark.parametrize("path", ["/history/.hidden", "/audio/a%20b/0", "/document/.x"])
def test_routes_reject_unsafe_ids(client, path):
    r = client.delete(path) if path.startswith("/document") else client.get(path)
    assert r.status_code == 400


def test_restore_rejects_unsafe_id(client):
    r = client.post("/restore", json={"doc_id": "../x", "chunks": [{"text": "hi"}]})
    assert r.status_code == 400


def test_upload_ids_are_unique_uuids(client):
    a = _upload(client).json()["doc_id"]
    client.delete(f"/document/{a}")
    b = _upload(client).json()["doc_id"]
    assert a != b
    assert len(a) == 32 and int(a, 16) >= 0


def test_legacy_numeric_history_still_loads(client):
    assert client.post("/history", json=_history_payload("1")).status_code == 200
    assert client.get("/history").json()[0]["doc_id"] == "1"
    assert client.get("/history/1").json()["chunks"][0]["text"] == "Hello there."


def test_audio_falls_back_to_history_after_restart(client):
    client.post("/history", json=_history_payload("1"))
    app_module.documents.clear()          # simulate a server restart
    r = client.get("/audio/1/0")
    assert r.status_code == 503            # found the doc; TTS just isn't running


def test_restore_replaces_existing_document(client):
    app_module.documents["1"] = {"chunks": [{"text": "WRONG DOC"}], "total_chunks": 1}
    payload = _history_payload("1", chunks=("Right one.", "Second."))["data"]
    assert client.post("/restore", json=payload).status_code == 200
    assert [c["text"] for c in app_module.documents["1"]["chunks"]] == ["Right one.", "Second."]


def test_restore_rejects_bad_chunks(client):
    assert client.post("/restore", json={"doc_id": "1", "chunks": "nope"}).status_code == 422


# ── Error handling ───────────────────────────────────────────────────────────

def test_history_missing_keys_is_422_not_500(client):
    assert client.post("/history", json={"doc_id": "z"}).status_code == 422


def test_negative_chunk_index_is_404(client):
    doc_id = _upload(client).json()["doc_id"]
    assert client.get(f"/audio/{doc_id}/-1").status_code == 404
    assert client.get(f"/audio/{doc_id}/999").status_code == 404


def test_unspeakable_chunk_returns_silence_without_tts(client):
    app_module.documents["s"] = {"chunks": [{"text": "—"}], "total_chunks": 1}
    r = client.get("/audio/s/0")
    assert r.status_code == 200 and r.content[:4] == b"RIFF"


def test_tts_down_is_503(client):
    doc_id = _upload(client).json()["doc_id"]
    assert client.get(f"/audio/{doc_id}/0").status_code == 503


def test_empty_pdf_is_rejected_with_hint(client):
    r = client.post("/upload", files={"file": ("scan.pdf", _pdf_bytes(), "application/pdf")})
    assert r.status_code == 422
    assert "scanned" in r.json()["detail"]


def test_non_pdf_and_corrupt_pdf(client):
    assert client.post("/upload", files={"file": ("a.txt", b"hi", "text/plain")}).status_code == 400
    assert client.post("/upload", files={"file": ("x.pdf", b"notapdf", "application/pdf")}).status_code == 500


def test_history_is_deduplicated(client, home):
    (home / "history.json").write_text(json.dumps([
        {"doc_id": "1", "filename": "old.pdf", "timestamp": "t1", "total_chunks": 1},
        {"doc_id": "1", "filename": "new.pdf", "timestamp": "t2", "total_chunks": 1},
        {"filename": "no id"},
    ]))
    entries = client.get("/history").json()
    assert [e["filename"] for e in entries] == ["new.pdf"]
    client.post("/history", json=_history_payload("1"))
    assert len(client.get("/history").json()) == 1


def test_delete_history_removes_file(client, home):
    client.post("/history", json=_history_payload("abc"))
    assert (home / "docs" / "abc.json").exists()
    assert client.delete("/history/abc").status_code == 200
    assert not (home / "docs" / "abc.json").exists()
    assert client.get("/history").json() == []


def test_index_served_regardless_of_cwd(client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = client.get("/")
    assert r.status_code == 200 and "Voxtral Reader" in r.text


# ── Voices ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("payload, expected", [
    ({"voices": ["casual_male", "fr_female"]}, ["casual_male", "fr_female"]),           # 0.4.x-style
    ({"object": "list", "data": [{"id": "af_heart", "name": "af_heart"}]}, ["af_heart"]),  # 0.5.x
    ({"object": "list", "model": "m", "data": []}, []),                                   # 0.5.x, Voxtral
    (["a", "b", "a"], ["a", "b"]),
    ({"data": [{"name": "x"}, {"voice": "y"}, 3, None]}, ["x", "y"]),
    ("garbage", []),
])
def test_parse_voices_handles_all_shapes(payload, expected):
    assert app_module._parse_voices(payload) == expected


def test_voices_falls_back_to_presets_when_tts_is_down(client):
    j = client.get("/voices").json()
    assert j["voices"] == app_module.PRESET_VOICES
    assert j["default"] == app_module.DEFAULT_VOICE


def test_health_reports_tts_status(client):
    assert client.get("/health").json() == {"app": "ok", "tts": False}


def test_ui_assets_and_module_imports_are_served(client):
    import re
    html = client.get("/").text
    assert '/static/css/app.css' in html and '/static/js/main.js' in html
    assert client.get("/static/css/app.css").status_code == 200
    # Follow every relative import from main.js; each module must be served
    # as JavaScript.
    seen, todo = set(), ["main.js"]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        r = client.get(f"/static/js/{name}")
        assert r.status_code == 200, name
        assert "javascript" in r.headers["content-type"], name
        todo += re.findall(r"from\s+'\./([\w.]+)'", r.text)
    assert {"api.js", "player.js", "library.js", "books.js", "reader.js", "ui.js"} <= seen
