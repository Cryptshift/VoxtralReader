# Voxtral Reader

A local scientific PDF reader that uses Mistral's Voxtral TTS model (via `mlx-audio`) to read papers aloud. Built for Apple Silicon (M-series). No cloud dependency — everything runs locally.

---

## Quick Start

```bash
cd ~/VoxtralReader
./start.sh          # starts mlx-audio TTS server + FastAPI app
# or double-click VoxtralReader.command in Finder
```

Opens at **http://localhost:7860**

---

## Architecture

```
VoxtralReader/
├── app.py               # FastAPI backend (all API routes)
├── pdf_processor.py     # PDF extraction + text cleaning pipeline
├── static/index.html    # Single-page UI (all JS inline, no build step)
├── requirements.txt
├── start.sh             # Starts both servers, warms up model
└── VoxtralReader.command  # Finder double-click launcher

~/.voxtral/              # Persistent data (auto-created)
├── history.json         # Document history metadata
└── docs/
    └── {doc_id}.json    # Full chunk data per document
```

Two servers run simultaneously:
- **Port 8000** — `mlx-audio` TTS server (OpenAI-compatible `/v1/audio/speech`)
- **Port 7860** — FastAPI app (serves UI + proxies TTS requests)

---

## Stack

| Layer | Tech |
|---|---|
| TTS model | `mistralai/Voxtral-4B-TTS-2603` via `mlx-community/Voxtral-4B-TTS-2603-mlx-bf16` |
| TTS server | `mlx-audio` (Apple Silicon / Metal, OpenAI-compatible API) |
| Backend | FastAPI + uvicorn |
| PDF extraction | PyMuPDF (`fitz`) |
| Frontend | Vanilla JS + HTML5 Audio, no framework |

---

## Installation

```bash
pip install -r requirements.txt
# requirements: fastapi, uvicorn[standard], httpx, pymupdf, pdfplumber,
#               python-multipart, mlx-audio>=0.4.0, webrtcvad
```

First run downloads the model (~8 GB from HuggingFace). Cached at `~/.cache/huggingface/`.

---

## How It Works

### PDF Processing (`pdf_processor.py`)

1. **Extraction** — PyMuPDF `get_text("blocks")` extracts paragraph-level blocks. Two-column layout is detected by classifying blocks as full-width / left-col / right-col based on block width and centre-x. Reading order: full-width → left column → right column per page.

2. **Cleaning** — aggressive pipeline runs before chunking:
   - Strip all citation forms: `[1]`, `[1,2]`, `[Smith et al., 2023]`, `(Author, Year)`
   - Remove LaTeX math → replace with `[equation]`
   - Remove URLs, DOIs, emails, footnote superscripts
   - Expand ~25 abbreviations (`Fig.` → `Figure N`, `e.g.,` → `for example,`, etc.)
   - Fix line-break hyphenation (ASCII and Unicode hyphens U+2010–U+2015)
   - Strip journal header noise (standalone numbers, short noise lines, trailing ISSNs)
   - References section split off — only appended if "Include References" toggle is on

3. **Chunking** — split at sentence boundaries, target ~160 chars per chunk (roughly one sentence). Shorter chunks = consistent TTS energy (avoids the model "running out of breath" on long inputs).

### Audio Playback (`static/index.html` — `Player` class)

- **Prefetch window**: 5 chunks ahead. Each fetch uses `AbortController` for cancellation.
- **Cached state**: chunks with ready audio get a green dot indicator.
- **Stop button**: aborts all in-flight fetch requests immediately, clears cache.
- Playback via HTML5 `<audio>` element. Seamless between chunks via prefetch.

### History Persistence

- On upload: full doc data POSTed to `/history` → saved to `~/.voxtral/docs/{id}.json`
- On page load: `GET /history` returns metadata only (fast). Full data lazy-loaded on first click.
- On server restart: clicking a history item calls `/restore` to re-register chunks with the in-memory `documents` dict.

---

## API Reference

| Method | Route | Description |
|---|---|---|
| `GET` | `/` | Serves `static/index.html` |
| `POST` | `/upload?include_refs=bool` | Upload PDF, returns `{ doc_id, sections, chunks, total_chunks }` |
| `GET` | `/audio/{doc_id}/{chunk_idx}?voice=X` | Generate + return WAV for one chunk |
| `POST` | `/restore` | Re-register doc data with in-memory store (used after server restart) |
| `DELETE` | `/document/{doc_id}` | Remove from in-memory store only |
| `GET` | `/history` | List history metadata |
| `POST` | `/history` | Save history entry + doc data to disk |
| `GET` | `/history/{doc_id}` | Load full doc data from disk |
| `DELETE` | `/history/{doc_id}` | Remove from history + disk + memory |
| `GET` | `/voices` | List available voices (proxies mlx-audio, falls back to preset list) |
| `GET` | `/health` | Check app + TTS server status |

---

## Configuration

Environment variables (set in shell or `.env`):

```bash
VLLM_URL=http://localhost:8000          # mlx-audio server URL
TTS_MODEL=mlx-community/Voxtral-4B-TTS-2603-mlx-bf16
DEFAULT_VOICE=casual_male
```

### Available Voices (20 presets)

`casual_male` `casual_female` `cheerful_female` `neutral_male` `neutral_female`
`fr_male` `fr_female` `es_male` `es_female` `de_male` `de_female`
`it_male` `it_female` `pt_male` `pt_female` `nl_male` `nl_female`
`ar_male` `hi_male` `hi_female`

### Workers

`start.sh` uses `--workers 4` for mlx-audio. Each worker handles one TTS request in parallel. On M4 Max this lets the 5-chunk prefetch fill concurrently. Reduce to `2` if you see memory pressure (model is ~8 GB per worker process).

---

## UI Features

- **Section TOC sidebar** — auto-detected from numbered/all-caps headings; click to jump
- **History sidebar tab** — persists across reloads; click to switch documents; × to delete
- **Playback controls** — ⏮ prev / ⏸▶ play-pause / ■ stop / ⏭ next paragraph
- **Progress bar** — clickable to seek to any paragraph
- **Voice selector** — dropdown of 20 presets
- **Speed selector** — 0.75× to 2×
- **Green dot indicator** — shows which chunks have cached audio ready
- **Keyboard shortcuts** — `Space` play/pause, `←/→` skip paragraph
- **Server status indicator** — green dot when mlx-audio is reachable

---

## Known Issues / Things To Improve

- **Page 1 journal headers** — affiliation sidebars on page 1 of journal articles still appear in output before the abstract. Could add a heuristic to skip blocks containing "Received:", "Correspondence:", department strings, etc.
- **Word-level highlighting** — chunk-level highlighting is implemented; word-level would need proportional timing estimation from `audio.currentTime / audio.duration` mapped across word count.
- **Very large PDFs** — history stores full chunk JSON in `~/.voxtral/docs/`. A 200-page book could be several MB. Fine for papers; worth watching for textbooks.
- **Equation handling** — equations are replaced with `[equation]` spoken literally. Could be skipped entirely with a toggle.
- **mlx-audio workers** — `--workers N` spawns N separate processes each loading the full model. If memory is tight, lower to 2 in `start.sh`.

---

## Development

```bash
# Run app only (assumes mlx-audio already running)
uvicorn app:app --host 0.0.0.0 --port 7860 --reload

# Run mlx-audio only
python -m mlx_audio.server --host 0.0.0.0 --port 8000 --workers 4

# Test PDF processing directly
python - <<'EOF'
from pdf_processor import process_pdf
doc = process_pdf("yourpaper.pdf")
print(f"{doc['total_chunks']} chunks, {len(doc['sections'])} sections")
for c in doc['chunks'][:5]:
    print(f"  [{c['section']}] {c['text'][:80]}")
EOF
```
