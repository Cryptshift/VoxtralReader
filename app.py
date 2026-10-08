import json
import os
import re
import struct
import tempfile
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse, Response

from pdf_processor import process_pdf
import books_reader

app = FastAPI(title="Voxtral Reader")

documents: dict = {}

# Set by run.py after the highlight overlay window is ready
_highlight_api = None

# State for the currently-loaded Books page
_books_state: dict = {
    "lines":  [],   # raw OCR lines from books_reader.ocr_page()
    "chunks": [],   # books_reader.lines_to_chunks() output (includes line_indices)
    "win":    None, # window info dict from books_reader
}

VLLM_URL     = os.getenv("VLLM_URL",     "http://localhost:8000")
TTS_MODEL    = os.getenv("TTS_MODEL",    "mlx-community/Voxtral-4B-TTS-2603-mlx-bf16")
DEFAULT_VOICE = os.getenv("DEFAULT_VOICE", "casual_male")

PRESET_VOICES = [
    "casual_male", "casual_female", "cheerful_female",
    "neutral_male", "neutral_female",
    "fr_male", "fr_female", "es_male", "es_female",
    "de_male", "de_female", "it_male", "it_female",
    "pt_male", "pt_female", "nl_male", "nl_female",
    "ar_male", "hi_male", "hi_female",
]

# ── Persistent history ────────────────────────────────────────────────────────
# VOXTRAL_HOME overrides the data directory (default: ~/.voxtral).
DATA_DIR  = Path(os.getenv("VOXTRAL_HOME") or Path.home() / ".voxtral").expanduser()
HIST_FILE = DATA_DIR / "history.json"
DOCS_DIR  = DATA_DIR / "docs"

# Document ids end up in file names, so only allow a conservative alphabet.
# Covers new UUID hex ids, legacy numeric ids ("0", "1", ...) and "books-<n>".
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def _check_id(doc_id) -> str:
    if not isinstance(doc_id, str) or not _SAFE_ID.match(doc_id):
        raise HTTPException(400, "Invalid document id")
    return doc_id


def _doc_path(doc_id: str) -> Path:
    path = DOCS_DIR / f"{_check_id(doc_id)}.json"
    # Belt and braces: never touch anything outside DOCS_DIR.
    if path.resolve().parent != DOCS_DIR.resolve():
        raise HTTPException(400, "Invalid document id")
    return path


def _get_doc(doc_id: str) -> dict | None:
    """Look a document up in memory, falling back to its saved history file
    (the in-memory store is empty after a server restart)."""
    _check_id(doc_id)
    doc = documents.get(doc_id)
    if doc is not None:
        return doc
    path = _doc_path(doc_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except Exception:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("chunks"), list):
        return None
    documents[doc_id] = data
    return data


def _dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

def _load_meta() -> list:
    try:
        return json.loads(HIST_FILE.read_text()) if HIST_FILE.exists() else []
    except Exception:
        return []

def _save_meta(entries: list):
    _dirs()
    HIST_FILE.write_text(json.dumps(entries, ensure_ascii=False, indent=2))

# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    return Path("static/index.html").read_text()


@app.post("/upload")
async def upload_pdf(file: UploadFile = File(...), include_refs: bool = Query(False)):
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are supported")
    content = await file.read()
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(content); tmp = f.name
    try:
        doc = process_pdf(tmp, include_references=include_refs)
    except Exception as e:
        raise HTTPException(500, f"PDF processing failed: {e}")
    finally:
        os.unlink(tmp)
    # Random ids: never reused across restarts, so they can't collide with
    # (and overwrite) documents already saved in history.
    doc_id = uuid.uuid4().hex
    documents[doc_id] = doc
    return {"doc_id": doc_id, **doc}


@app.get("/audio/{doc_id}/{chunk_idx}")
async def get_audio(doc_id: str, chunk_idx: int, voice: str = Query(DEFAULT_VOICE)):
    doc = _get_doc(doc_id)
    if doc is None:
        raise HTTPException(404, "Document not found")
    if chunk_idx >= len(doc["chunks"]):
        raise HTTPException(404, "Chunk index out of range")
    text = doc["chunks"][chunk_idx]["text"].strip()
    if not text or len(text) < 5:
        return Response(content=_silence_wav(), media_type="audio/wav")
    try:
        async with httpx.AsyncClient(timeout=600.0) as client:
            resp = await client.post(f"{VLLM_URL}/v1/audio/speech", json={
                "input": text, "model": TTS_MODEL,
                "response_format": "wav", "voice": voice,
            })
            resp.raise_for_status()
            return Response(content=resp.content, media_type="audio/wav")
    except httpx.ConnectError:
        raise HTTPException(503, "TTS server unreachable — is mlx-audio running?")
    except httpx.HTTPStatusError as e:
        body = e.response.text[:200]
        raise HTTPException(502, f"TTS server returned {e.response.status_code}: {body}")


@app.post("/restore")
async def restore_document(payload: dict):
    """(Re-)register a document the browser already has, e.g. after a restart.

    Always replaces whatever is in memory under that id, so the audio served
    for an id can never come from a different document.
    """
    doc_id = _check_id(payload.get("doc_id"))
    chunks = payload.get("chunks")
    if not isinstance(chunks, list) or not all(
        isinstance(c, dict) and isinstance(c.get("text"), str) for c in chunks
    ):
        raise HTTPException(422, "Document has no valid chunks")
    documents[doc_id] = {
        "sections":       payload.get("sections") or [{"name": "Document", "chunk_idx": 0}],
        "chunks":         chunks,
        "total_chunks":   len(chunks),
        "has_references": bool(payload.get("has_references")),
    }
    return {"ok": True}


@app.delete("/document/{doc_id}")
async def delete_document(doc_id: str):
    documents.pop(_check_id(doc_id), None)
    return {"ok": True}


# ── History API ───────────────────────────────────────────────────────────────

@app.get("/history")
async def get_history():
    return _load_meta()


@app.post("/history")
async def save_history(payload: dict):
    _dirs()
    doc_id = payload["doc_id"]
    # Persist full doc data separately
    _doc_path(doc_id).write_text(
        json.dumps(payload["data"], ensure_ascii=False)
    )
    # Update metadata list
    meta = {k: payload[k] for k in ("doc_id","filename","timestamp","total_chunks")}
    entries = [e for e in _load_meta() if e["doc_id"] != doc_id]
    entries.append(meta)
    _save_meta(entries)
    return {"ok": True}


@app.get("/history/{doc_id}")
async def get_doc_data(doc_id: str):
    path = _doc_path(doc_id)
    if not path.exists():
        raise HTTPException(404, "Document not found in history")
    return json.loads(path.read_text())


@app.delete("/history/{doc_id}")
async def delete_history(doc_id: str):
    doc_path = _doc_path(doc_id)
    entries = [e for e in _load_meta() if e["doc_id"] != doc_id]
    _save_meta(entries)
    if doc_path.exists():
        doc_path.unlink()
    documents.pop(doc_id, None)
    return {"ok": True}


# ── Misc ──────────────────────────────────────────────────────────────────────

# ── Books integration ─────────────────────────────────────────────────────────

@app.get("/books/status")
async def books_status():
    win = books_reader.get_books_window()
    return {
        "available":   books_reader.available(),
        "books_open":  win is not None,
        "window":      win,
    }


@app.post("/books/capture")
async def books_capture():
    """Screenshot + OCR the current Books page and register it as a document."""
    global _books_state

    lines, win = books_reader.ocr_page()
    if not lines or win is None:
        raise HTTPException(
            503,
            "Books window not found — make sure Books is open and Screen "
            "Recording permission is granted in System Settings."
        )

    # Separate heading/title lines from body text
    page_title, body_lines = books_reader.extract_title(lines)

    chunks = books_reader.lines_to_chunks(body_lines)
    if not chunks:
        raise HTTPException(422, "No readable text found on this page")

    _books_state["lines"]  = body_lines
    _books_state["chunks"] = chunks
    _books_state["win"]    = win

    if _highlight_api:
        _highlight_api.clear()

    doc_id    = f"books-{win['id']}"
    section   = page_title or "Books Page"
    tts_chunks = [
        {"text": c["text"], "section": section, "index": c["index"]}
        for c in chunks
    ]
    documents[doc_id] = {
        "sections":       [{"name": section, "chunk_idx": 0}],
        "chunks":         tts_chunks,
        "total_chunks":   len(tts_chunks),
        "has_references": False,
    }

    return {
        "doc_id":       doc_id,
        "page_title":   page_title,
        "sections":     documents[doc_id]["sections"],
        "chunks":       tts_chunks,
        "total_chunks": len(tts_chunks),
    }


@app.post("/books/highlight/{chunk_idx}")
async def books_highlight(chunk_idx: int):
    """Update the highlight overlay for the given chunk index."""
    chunks = _books_state["chunks"]
    lines  = _books_state["lines"]
    win    = _books_state["win"]

    if not chunks or win is None or chunk_idx >= len(chunks):
        return {"ok": False}

    line_indices = chunks[chunk_idx].get("line_indices", [])
    ww, wh = win["width"], win["height"]

    rects = []
    try:
        from AppKit import NSScreen
        screen_h = NSScreen.mainScreen().frame().size.height
    except Exception:
        screen_h = 1440  # safe fallback

    for i in line_indices:
        if 0 <= i < len(lines):
            bx, by, bw, bh = lines[i]["bbox"]
            # Vision bbox: normalised, bottom-left origin, relative to window image.
            # Convert to AppKit screen coords (bottom-left origin, points).
            # Quartz win['y'] is from top of screen → AppKit y is from bottom.
            appkit_win_y = screen_h - win["y"] - win["height"]
            rects.append([
                round(win["x"] + bx * ww, 1),
                round(appkit_win_y + by * wh, 1),
                round(bw * ww, 1),
                round(bh * wh, 1),
            ])

    if _highlight_api:
        _highlight_api.set_rects(rects)

    return {"ok": True, "rects": len(rects)}


@app.post("/books/next_page")
async def books_next_page():
    """Clear highlights and advance one page in Books."""
    if _highlight_api:
        _highlight_api.clear()
    books_reader.turn_page()
    return {"ok": True}


@app.get("/voices")
async def list_voices():
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{VLLM_URL}/v1/audio/voices")
            if r.status_code == 200:
                return r.json()
    except Exception:
        pass
    return {"voices": PRESET_VOICES}


@app.get("/health")
async def health():
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"{VLLM_URL}/v1/models")
            vllm_ok = r.status_code == 200
    except Exception:
        vllm_ok = False
    return {"app": "ok", "vllm": vllm_ok}


def _silence_wav() -> bytes:
    sr, n = 24000, 2400
    hdr = struct.pack("<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36+n*2, b"WAVE", b"fmt ", 16, 1, 1,
        sr, sr*2, 2, 16, b"data", n*2)
    return hdr + b"\x00" * (n * 2)
