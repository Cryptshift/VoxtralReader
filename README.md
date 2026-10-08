# Voxtral Reader

Reads PDFs (and pages of the macOS Books app) aloud with Mistral's Voxtral
text-to-speech model, running locally on Apple Silicon through
[`mlx-audio`](https://github.com/Blaizzy/mlx-audio). After the one-time model
download, nothing leaves your Mac.

- Cleans scientific PDFs before reading: citations stripped, math replaced,
  abbreviations expanded, references optional.
- Section sidebar, click-to-seek, 20 voices, 0.75–2× speed.
- History of opened documents that survives restarts.
- Generated audio is cached on disk, so paragraphs you've already heard play
  instantly.
- **Books mode**: OCRs the page open in Apple Books, reads it, turns the page
  and carries on.

---

## Requirements

- Apple Silicon Mac running macOS 14 or later (MLX needs it)
- [uv](https://docs.astral.sh/uv/) (recommended), or Python 3.10+ and pip
- About 8 GB of disk for the model and enough free memory to hold it
  (one copy of the ~8 GB model is loaded)

## Install

```bash
cd VoxtralReader
uv sync
```

`uv sync` creates `.venv/` with the Python version in `.python-version`
(3.13) and the exact package versions in `uv.lock`. Both launchers use that
`.venv`.

Without uv:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

`requirements.txt` is exported from `uv.lock`, so it has the same pinned
versions. mlx-audio is pinned to **0.5.8**: its server API changes between
minor versions, so upgrade it on purpose (see Development).

### The model

The first time audio is requested, mlx-audio downloads
[`mlx-community/Voxtral-4B-TTS-2603-mlx-bf16`](https://huggingface.co/mlx-community/Voxtral-4B-TTS-2603-mlx-bf16)
(~8 GB) into `~/.cache/huggingface/`. The weights are **not** part of this
repository. They come from
[`mistralai/Voxtral-4B-TTS-2603`](https://huggingface.co/mistralai/Voxtral-4B-TTS-2603)
and are licensed **CC BY-NC (non-commercial use only)**, separately from this
code.

Once the model has been downloaded, you can run with `HF_HUB_OFFLINE=1` to keep
mlx-audio from contacting Hugging Face at all.

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
- Runs with the project's `.venv`. If there isn't one, it tells you to run
  `uv sync` and stops. To use another environment, set `PYTHON`, for example
  `PYTHON=python ./start.sh`.
- If a port is taken by another program, it tells you which one and exits.
  It never kills anything.
- Ctrl+C stops the app and the TTS server it started.

`VoxtralReader.command` runs `start.sh` in Terminal and opens your browser as
soon as the app responds (allowing up to 15 minutes for the first model
download).

Environment overrides: `APP_PORT`, `TTS_PORT`, `TTS_MODEL`, `TTS_MAX_BATCH`,
`PYTHON`, for example `APP_PORT=7861 ./start.sh`.

### As a desktop window: `uv run run.py`

```bash
uv run run.py          # or: .venv/bin/python run.py
```

Opens a frameless, always-on-top reader window (pywebview) with its own
close / minimize / pin buttons. Drag the window by its header bar. It starts
the TTS server if needed, logging to `logs/tts.log`, and stops it when you
close the window. The model loads on the first request, so the first
paragraph takes a little longer. `APP_PORT` and `TTS_PORT` work here too.

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

---

## How it works

```
VoxtralReader/
├── app.py               # FastAPI backend: upload, audio proxy, history, cache, Books routes
├── audio_cache.py       # On-disk audio cache (atomic writes, LRU size cap)
├── pdf_processor.py     # PDF extraction + text cleaning + chunking
├── books_reader.py      # Books window capture, Vision OCR, reading order, page turns
├── static/
│   ├── index.html       # Markup only
│   ├── css/app.css
│   └── js/              # Plain ES modules, no build step:
│       ├── main.js      #   entry point, wires everything together
│       ├── api.js       #   fetch wrappers for the server routes
│       ├── player.js    #   playback engine (fetch, prefetch, retry, cancel)
│       ├── state.js     #   shared player instance and open document
│       ├── reader.js    #   document view (paragraphs, sections)
│       ├── library.js   #   History tab
│       ├── books.js     #   Books mode
│       ├── cache.js     #   audio cache readout / Clear
│       └── ui.js        #   toast, status, tabs, progress bar, window controls
├── run.py               # Desktop-window launcher (pywebview)
├── start.sh             # Browser launcher
├── VoxtralReader.command  # Finder double-click launcher (runs start.sh)
├── pyproject.toml       # Dependencies (uv); uv.lock pins them
├── requirements.txt     # Exported from uv.lock for pip users
└── tests/               # pytest suite (no model needed)
```

Two local servers:

- **127.0.0.1:8000**: `mlx-audio` TTS server (OpenAI-style `/v1/audio/speech`).
  One process holds the model. It's started with `--tts-max-batch-size 3` (the
  playing paragraph plus two prefetched), but Voxtral doesn't support batching
  in mlx-audio 0.5.8, so requests run one at a time in order.
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

### Playback (`static/js/player.js`)

- When a chunk is ready and playing, the next 2 chunks are generated in the
  background. Chunks with audio ready get a green dot.
- Failed requests are retried. If the app server lost the document (for
  example after a restart), the page re-sends it via `/restore` and retries.
  If a chunk still can't be generated, playback stops with an error message
  instead of skipping ahead.
- Stop, voice changes and opening another document cancel pending requests,
  and the TTS server stops generating them. The app asks mlx-audio for
  streamed PCM (2 s of audio per piece), wraps it in a WAV, and closes the
  upstream request when the browser goes away. mlx-audio then cancels at the
  next piece instead of finishing the paragraph. (Measured: after cancelling
  an 8 s paragraph 1.5 s in, the next request finished in about 1.5 s instead
  of waiting about 8 s.)

### Audio cache (`audio_cache.py`)

Every generated paragraph is saved as a WAV under `~/.voxtral/cache/`, keyed by
a SHA-256 of the model id, voice, generation parameters and the exact text.
Replaying a paragraph, reopening a document or switching back to a voice is
served from disk in a few milliseconds without touching the TTS server.

- Files are written to a temp file and renamed into place, so a cancelled or
  failed request never leaves a partial file.
- The cache is capped at 2 GB by default (`VOXTRAL_AUDIO_CACHE_MB`; `0` turns
  it off). When it's full, the least recently played files are deleted first.
- The sidebar shows the current size; **Clear** empties it.
- Playback speed is applied in the browser, so it doesn't create separate
  cache entries.

### History and data

Stored in `~/.voxtral/` (set `VOXTRAL_HOME` to use another directory):

```
~/.voxtral/
├── history.json     # metadata, one entry per document
├── docs/{id}.json   # full chunk data per document
└── cache/           # generated audio (see above)
```

Document ids are random UUIDs. Histories from older versions, which used
numeric ids, still load.

---

## API

| Method | Route | Description |
|---|---|---|
| `GET` | `/` | The UI (assets under `/static/`) |
| `POST` | `/upload?include_refs=bool` | Upload a PDF → `{doc_id, sections, chunks, total_chunks, has_references}`. 422 if it has no text. |
| `GET` | `/audio/{doc_id}/{chunk_idx}?voice=X` | WAV for one chunk, from the cache if possible (`X-Cache: hit`/`miss`). Falls back to the saved history copy if the doc isn't in memory. |
| `POST` | `/restore` | Re-register a document's chunks under its id (replaces any in-memory copy) |
| `DELETE` | `/document/{doc_id}` | Remove from memory only |
| `GET` | `/history` | History metadata |
| `POST` | `/history` | Save `{doc_id, filename, timestamp, total_chunks, data}` |
| `GET` | `/history/{doc_id}` | Full saved document |
| `DELETE` | `/history/{doc_id}` | Remove from history, disk and memory |
| `GET` | `/books/status` | Whether Books integration is available and a Books window is open |
| `POST` | `/books/capture` | OCR the current Books page and register it as a document |
| `POST` | `/books/next_page` | Turn the page, wait for it to settle and return it (409 if it didn't change) |
| `GET` | `/cache` | Audio cache `{bytes, files, max_bytes}` |
| `DELETE` | `/cache` | Empty the audio cache |
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
| `TTS_SAMPLE_RATE` | `24000` | app (sample rate of the model's PCM output) |
| `VOXTRAL_HOME` | `~/.voxtral` | app |
| `VOXTRAL_AUDIO_CACHE_MB` | `2048` | app (`0` disables the audio cache) |
| `APP_PORT` / `TTS_PORT` | `7860` / `8000` | start.sh, run.py, VoxtralReader.command (`APP_PORT`) |
| `TTS_MAX_BATCH` | `3` | start.sh, run.py (`--tts-max-batch-size`) |
| `PYTHON` | `.venv/bin/python` | start.sh |

Set these in your shell. There is no `.env` file support.

### Voices (20 presets)

`casual_male` `casual_female` `cheerful_female` `neutral_male` `neutral_female`
`fr_male` `fr_female` `es_male` `es_female` `de_male` `de_female`
`it_male` `it_female` `pt_male` `pt_female` `nl_male` `nl_female`
`ar_male` `hi_male` `hi_female`

---

## Development

```bash
uv sync                     # includes the dev group (pytest)
uv run pytest               # fast; no model or TTS server needed
```

Run the pieces separately:

```bash
uv run python -m mlx_audio.server --host 127.0.0.1 --port 8000 --tts-max-batch-size 3
uv run uvicorn app:app --host 127.0.0.1 --port 7860
```

Changing dependencies: edit `pyproject.toml`, then

```bash
uv lock
uv export --frozen --no-dev --no-hashes --no-annotate -o requirements.txt
```

Notes on the pins: mlx-audio's `tts` extra provides the mistral-common
tokenizer Voxtral needs. Its `server` extra isn't used because it pulls
`webrtcvad`, which has to be compiled. `webrtcvad-wheels` provides the same
module prebuilt.

## Known limitations

- Affiliation sidebars on page 1 of journal articles can still be read before
  the abstract.
- Equations are read as "[equation]".
- Highlighting is per chunk, not per word. Books mode highlights only in
  the reader window, not on the Books page itself.
- Very large PDFs make large history files (`~/.voxtral/docs/`).

## License

The code is MIT licensed (see [LICENSE](LICENSE)). The Voxtral model weights
are not included and are licensed separately under CC BY-NC by Mistral AI.
