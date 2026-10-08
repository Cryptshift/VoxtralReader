# Voxtral Reader

Reads PDFs (and pages of the macOS Books app) aloud with Mistral's Voxtral
text-to-speech model, running locally on Apple Silicon through
[`mlx-audio`](https://github.com/Blaizzy/mlx-audio). After the one-time model
download, nothing leaves your Mac.

- Cleans scientific PDFs before reading: citations stripped, math replaced,
  abbreviations expanded, references optional.
- Section sidebar, click-to-seek, 20 voices, 0.75–2× speed.
- History of opened documents that survives restarts.
- **Books mode**: OCRs the page open in Apple Books, reads it, turns the page
  and carries on.

---

## Requirements

- Apple Silicon Mac running macOS 14 or later (MLX needs it)
- Python 3.10+ (tested with 3.13)
- About 8 GB of disk for the model and enough free memory to hold it
  (one copy of the ~8 GB model is loaded)

## Install

```bash
cd VoxtralReader
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`requirements.txt` pins versions that are known to work together, including
**mlx-audio 0.4.2**. mlx-audio 0.5 changed its server, so don't upgrade it
on its own.

### The model

The first time audio is requested, mlx-audio downloads
[`mlx-community/Voxtral-4B-TTS-2603-mlx-bf16`](https://huggingface.co/mlx-community/Voxtral-4B-TTS-2603-mlx-bf16)
(~8 GB) into `~/.cache/huggingface/`. The weights are **not** part of this
repository. They come from
[`mistralai/Voxtral-4B-TTS-2603`](https://huggingface.co/mistralai/Voxtral-4B-TTS-2603)
and are licensed **CC BY-NC (non-commercial use only)**, separately from this
code.

---

## Running it

There are two launchers. Both bind only to `127.0.0.1`, so nothing is exposed
to your network.

### In the browser: `./start.sh` (or double-click `VoxtralReader.command`)

```bash
./start.sh            # then open http://127.0.0.1:7860
```

- Starts the mlx-audio TTS server on port 8000, or reuses one that's already
  running. Its output goes to `logs/tts.log`.
- Loads the model in the background, then serves the app on port 7860.
- Uses `.venv/bin/python` if the project has a `.venv`, otherwise `python` on
  your `PATH`.
- If a port is taken by another program, it tells you which one and exits.
  It never kills anything.
- Ctrl+C stops the app and the TTS server it started.

`VoxtralReader.command` runs `start.sh` in Terminal and opens your browser as
soon as the app responds (allowing up to 15 minutes for the first model
download).

Environment overrides: `APP_PORT`, `TTS_PORT`, `TTS_MODEL`, `PYTHON`, for
example `APP_PORT=7861 ./start.sh`.

### As a desktop window: `python run.py`

```bash
source .venv/bin/activate
python run.py
```

Opens a frameless, always-on-top reader window (pywebview) with its own
close / minimize / pin buttons. It starts the TTS server if needed, logging to
`logs/tts.log`, and stops it when you close the window. The model loads on the
first request, so the first paragraph takes a little longer. `APP_PORT` and
`TTS_PORT` work here too.

### Keyboard

`Space` play/pause · `←` / `→` previous / next paragraph · ■ stops playback and
cancels pending requests.

---

## Books mode

Click **Read Books** with a book open in Apple Books. Voxtral Reader takes a
screenshot of the Books window, runs Apple's Vision OCR on it and reads the
page. At the end of the page it turns the page (right arrow), waits for the
new page to settle, and keeps reading. It stops on its own if the page doesn't
change, for example at the end of the book or if the key press was blocked.
Two-page spreads are read left page first, then right.

macOS asks for permissions the first time. Grant them to the app that runs
Voxtral Reader (Terminal, or the Python binary):

| Permission (System Settings → Privacy & Security) | Needed for |
|---|---|
| **Screen Recording** | capturing the Books window |
| **Accessibility** | sending the page-turn key press |
| **Automation** (Books, System Events) | bringing Books to the front to turn the page |

The on-screen highlight overlay for Books mode is not implemented yet. The
highlight endpoint computes rectangles, but the overlay in `run.py` is a stub.

---

## How it works

```
VoxtralReader/
├── app.py               # FastAPI backend: upload, audio proxy, history, Books routes
├── pdf_processor.py     # PDF extraction + text cleaning + chunking
├── books_reader.py      # Books window capture, Vision OCR, reading order, page turns
├── static/index.html    # Single-page UI (vanilla JS, no build step)
├── run.py               # Desktop-window launcher (pywebview)
├── start.sh             # Browser launcher
├── VoxtralReader.command  # Finder double-click launcher (runs start.sh)
└── tests/               # pytest suite (no model needed)
```

Two local servers:

- **127.0.0.1:8000**: `mlx-audio` TTS server (OpenAI-style `/v1/audio/speech`), one worker
- **127.0.0.1:7860**: the FastAPI app (UI, PDF processing, proxies TTS requests)

### PDF processing (`pdf_processor.py`)

1. **Extraction**: PyMuPDF text blocks. Two-column pages are detected from
   block width and position. Each column is read top to bottom, left column
   first, with full-width blocks placed where they appear.
2. **Cleaning**:
   - citations (`[1]`, `[1,2]`, `[Smith et al., 2023]`, `(Author, Year)`,
     superscript markers like `shown1,2`) are removed, while decimals,
     versions and symbols (`0.01`, `3.12`, `x2`) are kept
   - LaTeX math becomes `[equation]`
   - URLs, DOIs and emails are removed
   - about 20 abbreviations are expanded (`Fig. 3` → `Figure 3`, `e.g.` → `for example`)
   - line-break hyphenation is fixed
   - page numbers and noise lines are removed
3. **References**: the last "References"/"Bibliography" heading in the back
   part of the document starts the references, which are only read when
   **Include References** is on. A "References" line in a table of contents
   is ignored.
4. **Sections**: numbered (`2 Related Work`, `3.1 …`), roman (`IV. …`) and
   all-caps headings. Wrapped body lines that merely start with a number are
   ignored.
5. **Chunking**: sentences grouped into chunks of about 160 characters. Short
   sentences such as "Yes." are kept.

PDFs without a text layer (scanned images) are rejected with a message. Run
them through OCR first.

### Playback (`static/index.html`)

- When a chunk is ready and playing, the next 2 chunks are generated in the
  background. Chunks with audio ready get a green dot.
- Failed requests are retried. If the app server lost the document (for
  example after a restart), the page re-sends it via `/restore` and retries.
  If a chunk still can't be generated, playback stops with an error message
  instead of skipping ahead.
- Stop, voice changes and opening another document cancel the browser's
  pending requests. With mlx-audio 0.4.2, a generation that has already
  started on the TTS server still runs to completion.

### History and data

Stored in `~/.voxtral/` (set `VOXTRAL_HOME` to use another directory):

```
~/.voxtral/
├── history.json     # metadata, one entry per document
└── docs/{id}.json   # full chunk data per document
```

Document ids are random UUIDs. Histories from older versions, which used
numeric ids, still load.

---

## API

| Method | Route | Description |
|---|---|---|
| `GET` | `/` | The UI |
| `POST` | `/upload?include_refs=bool` | Upload a PDF → `{doc_id, sections, chunks, total_chunks, has_references}`. 422 if it has no text. |
| `GET` | `/audio/{doc_id}/{chunk_idx}?voice=X` | WAV for one chunk (falls back to the saved history copy if the doc isn't in memory) |
| `POST` | `/restore` | Re-register a document's chunks under its id (replaces any in-memory copy) |
| `DELETE` | `/document/{doc_id}` | Remove from memory only |
| `GET` | `/history` | History metadata |
| `POST` | `/history` | Save `{doc_id, filename, timestamp, total_chunks, data}` |
| `GET` | `/history/{doc_id}` | Full saved document |
| `DELETE` | `/history/{doc_id}` | Remove from history, disk and memory |
| `GET` | `/books/status` | Whether Books integration is available and a Books window is open |
| `POST` | `/books/capture` | OCR the current Books page and register it as a document |
| `POST` | `/books/next_page` | Turn the page, wait for it to settle and return it (409 if it didn't change) |
| `POST` | `/books/highlight/{chunk_idx}` | Compute highlight rectangles for a chunk (overlay not implemented yet) |
| `POST` | `/books/clear` | Clear highlights |
| `GET` | `/voices` | `{voices, default}`, from the TTS server or the 20 presets |
| `GET` | `/health` | `{app: "ok", tts: bool}` |

Document ids must match `[A-Za-z0-9][A-Za-z0-9_-]{0,63}`.

---

## Configuration

| Variable | Default | Used by |
|---|---|---|
| `TTS_URL` | `http://127.0.0.1:8000` | app (`VLLM_URL` is accepted as an older name) |
| `TTS_MODEL` | `mlx-community/Voxtral-4B-TTS-2603-mlx-bf16` | app, start.sh |
| `DEFAULT_VOICE` | `casual_male` | app |
| `VOXTRAL_HOME` | `~/.voxtral` | app |
| `APP_PORT` / `TTS_PORT` | `7860` / `8000` | start.sh, run.py, VoxtralReader.command (`APP_PORT`) |
| `PYTHON` | `.venv/bin/python` or `python` | start.sh |

Set these in your shell. There is no `.env` file support.

### Voices (20 presets)

`casual_male` `casual_female` `cheerful_female` `neutral_male` `neutral_female`
`fr_male` `fr_female` `es_male` `es_female` `de_male` `de_female`
`it_male` `it_female` `pt_male` `pt_female` `nl_male` `nl_female`
`ar_male` `hi_male` `hi_female`

---

## Development

```bash
pip install -r requirements-dev.txt
pytest                      # fast; no model or TTS server needed
```

Run the pieces separately:

```bash
python -m mlx_audio.server --host 127.0.0.1 --port 8000
uvicorn app:app --host 127.0.0.1 --port 7860
```

## Known limitations

- Affiliation sidebars on page 1 of journal articles can still be read before
  the abstract.
- Equations are read as "[equation]".
- Highlighting is per chunk, not per word. Books mode has no on-screen
  highlight yet.
- Very large PDFs make large history files (`~/.voxtral/docs/`).

## License

The code is MIT licensed (see [LICENSE](LICENSE)). The Voxtral model weights
are not included and are licensed separately under CC BY-NC by Mistral AI.
