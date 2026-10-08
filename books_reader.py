"""
books_reader.py
Reads text from the macOS Books app via screenshot + Vision OCR.

Requires (System Settings > Privacy & Security):
  - Screen Recording  — to capture the Books window
  - Accessibility     — to send page-turn key events
"""
from __future__ import annotations

import difflib
import re
import subprocess
from typing import Any

try:
    import Quartz
    import Vision
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False


def available() -> bool:
    return _AVAILABLE


# ── Books window detection ─────────────────────────────────────────────────────

def get_books_window() -> dict | None:
    """
    Return bounds of the frontmost Books reading window, or None.
    Bounds are in screen points, Quartz coordinate system (top-left origin).
    """
    if not _AVAILABLE:
        return None
    windows = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly,
        Quartz.kCGNullWindowID,
    )
    for w in windows:
        if (w.get('kCGWindowOwnerName') == 'Books'
                and w.get('kCGWindowLayer', 99) == 0
                and w.get('kCGWindowBounds', {}).get('Width', 0) > 200):
            b = w['kCGWindowBounds']
            return {
                'id':     int(w['kCGWindowNumber']),
                'x':      int(b['X']),
                'y':      int(b['Y']),
                'width':  int(b['Width']),
                'height': int(b['Height']),
            }
    return None


# ── Screenshot ─────────────────────────────────────────────────────────────────

def _screenshot(win: dict) -> Any:
    """Capture the Books window as a CGImage (device resolution)."""
    return Quartz.CGWindowListCreateImage(
        Quartz.CGRectMake(win['x'], win['y'], win['width'], win['height']),
        Quartz.kCGWindowListOptionIncludingWindow,
        win['id'],
        Quartz.kCGWindowImageDefault,
    )


# ── Vision OCR ─────────────────────────────────────────────────────────────────

def ocr_page() -> tuple[list[dict], dict | None]:
    """
    Screenshot + OCR the current Books page.

    Returns (lines, window_info).
    Each line dict: { 'text': str, 'bbox': (x, y, w, h) }
      - bbox values are normalised 0-1, origin at bottom-left (Vision convention).
    Lines are sorted top-to-bottom (reading order).
    Returns ([], None) on any failure.
    """
    if not _AVAILABLE:
        return [], None

    win = get_books_window()
    if not win:
        return [], None

    image = _screenshot(win)
    if not image:
        return [], None

    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setUsesLanguageCorrection_(True)

    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(image, {})
    try:
        handler.performRequests_error_([request], None)
    except Exception:
        pass

    lines: list[dict] = []
    for obs in (request.results() or []):
        top = obs.topCandidates_(1)
        if not top:
            continue
        text = top[0].string()
        if not text or not text.strip():
            continue
        bb = obs.boundingBox()  # NSRect, normalised, bottom-left origin
        lines.append({
            'text': text.strip(),
            'bbox': (bb.origin.x, bb.origin.y, bb.size.width, bb.size.height),
        })

    return sort_reading_order(lines), win


# ── Reading order ──────────────────────────────────────────────────────────────

def _top(line: dict) -> float:
    """Top edge of a line in Vision coordinates (higher = closer to the top)."""
    return line['bbox'][1] + line['bbox'][3]


def sort_reading_order(lines: list[dict]) -> list[dict]:
    """
    Order OCR lines for reading.

    Normally that is simply top-to-bottom. When Books shows a two-page spread
    (lines sit entirely in the left or right half of the window), read the
    whole left page first, then the right page; sorting the spread by height
    alone would alternate between the two pages line by line.
    """
    by_height = sorted(lines, key=lambda l: -_top(l))
    left, right, spanning = [], [], []
    for line in lines:
        x, _, w, _ = line['bbox']
        if x + w <= 0.52:
            left.append(line)
        elif x >= 0.48:
            right.append(line)
        else:
            spanning.append(line)

    is_spread = len(left) >= 3 and len(right) >= 3 and len(spanning) <= 0.2 * len(lines)
    if not is_spread:
        return by_height

    # Lines crossing the gutter above both pages (e.g. a running title) come
    # first; any others are read with the left page.
    highest = max(_top(l) for l in left + right)
    head = [l for l in spanning if _top(l) >= highest]
    rest = [l for l in spanning if _top(l) < highest]
    key = lambda l: -_top(l)
    return sorted(head, key=key) + sorted(left + rest, key=key) + sorted(right, key=key)


# ── Page comparison ────────────────────────────────────────────────────────────

def page_text(lines: list[dict]) -> str:
    return " ".join(" ".join(l['text'].split()) for l in lines)


def same_page(a: str, b: str, threshold: float = 0.95) -> bool:
    """True when two OCR captures are (almost) the same text, i.e. the page
    didn't change. Tolerates small OCR differences between captures."""
    if a == b:
        return True
    if not a or not b:
        return False
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio() >= threshold


# ── Heading / title detection ──────────────────────────────────────────────────

def extract_title(lines: list[dict]) -> tuple[str | None, list[dict]]:
    """
    Detect a page title by looking for an unusually large vertical gap after
    the first line — the whitespace the user can see between heading and body.

    Vision bbox: (x, y, w, h), y = bottom edge of line, 0=image bottom, 1=top.
    Lines are already in reading order (see sort_reading_order).

    Gap between consecutive lines i and i+1:
      gap = lines[i].y  -  (lines[i+1].y + lines[i+1].h)
    (positive = whitespace; negative = overlapping, treated as 0)
    """
    if len(lines) < 2:
        return None, lines

    def _gap(a: dict, b: dict) -> float:
        """Vertical whitespace between line a (above) and line b (below)."""
        return max(0.0, a["bbox"][1] - (b["bbox"][1] + b["bbox"][3]))

    # Gap between line 0 and line 1 (the candidate title gap)
    title_gap = _gap(lines[0], lines[1])

    # Median gap across the rest of the body (lines 1 onward)
    body_gaps = sorted(
        _gap(lines[i], lines[i + 1]) for i in range(1, len(lines) - 1)
    )
    median_body_gap = body_gaps[len(body_gaps) // 2] if body_gaps else 0.0

    # Title detected when:
    #   • gap after line 0 is at least 2× larger than typical body spacing
    #   • AND at least 0.02 normalised units (catches cases where body gap ≈ 0)
    #   • AND the line doesn't end mid-sentence
    first_line_text = lines[0]["text"].strip()
    big_gap    = title_gap >= max(2.0 * median_body_gap, median_body_gap + 0.02)
    not_sentence = not first_line_text[-1:] in ".,:;"

    if big_gap and not_sentence:
        return first_line_text, lines[1:]

    return None, lines


# ── Group OCR lines into TTS chunks ───────────────────────────────────────────

def _sanitize(text: str) -> str:
    """Normalize Unicode punctuation to ASCII so the TTS model handles it cleanly."""
    replacements = {
        "‘": "'", "’": "'",   # smart single quotes
        "“": '"', "”": '"',   # smart double quotes
        "–": "-", "—": "-",   # en-dash, em-dash
        "…": "...",                # ellipsis
        " ": " ",                  # non-breaking space
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    return text


def _join_lines(parts: list[str]) -> str:
    """Join OCR text lines, resolving line-break hyphens (e.g. 'fea-' + 'ture' -> 'feature')."""
    if not parts:
        return ''
    result = parts[0]
    for part in parts[1:]:
        if result.endswith('-') and result[:-1][-1:].isalpha() and part[:1].islower():
            result = result[:-1] + part
        else:
            result += ' ' + part
    return result


def lines_to_chunks(lines: list[dict], max_chars: int = 160) -> list[dict]:
    """
    Group adjacent OCR lines into TTS chunks of at most max_chars each.
    Each chunk records which line indices it covers so the highlight
    overlay knows which rectangles to draw.
    """
    chunks: list[dict] = []
    bucket_lines: list[int] = []
    bucket_parts: list[str] = []
    bucket_len = 0

    for i, line in enumerate(lines):
        text = _sanitize(line['text'].strip())
        if not text:
            continue
        if bucket_len + len(text) + 1 > max_chars and bucket_lines:
            chunks.append({
                'text':         _join_lines(bucket_parts),
                'line_indices': bucket_lines[:],
                'section':      'Books',
                'index':        len(chunks),
            })
            bucket_lines = [i]
            bucket_parts = [text]
            bucket_len   = len(text)
        else:
            bucket_lines.append(i)
            bucket_parts.append(text)
            bucket_len += len(text) + 1

    if bucket_lines:
        chunks.append({
            'text':         _join_lines(bucket_parts),
            'line_indices': bucket_lines[:],
            'section':      'Books',
            'index':        len(chunks),
        })

    # Keep short sentences ("Yes.") but drop punctuation-only crumbs.
    return [c for c in chunks if re.search(r'[^\W_]{2,}', c['text'])]


# ── Page turn ─────────────────────────────────────────────────────────────────

# System Events sends key codes to whichever app is frontmost, not to the
# process named in the tell block, so Books has to be activated first.
_TURN_PAGE_SCRIPT = """
tell application "Books" to activate
delay 0.3
tell application "System Events" to tell process "Books" to key code 124
"""


class PageTurnError(RuntimeError):
    pass


def turn_page() -> None:
    """Advance one page in Books (right-arrow key via System Events).

    Raises PageTurnError with a user-facing message if osascript fails,
    typically because the Accessibility or Automation permission is missing.
    """
    try:
        r = subprocess.run(
            ['osascript', '-e', _TURN_PAGE_SCRIPT],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise PageTurnError(f"Could not run osascript: {e}") from e
    if r.returncode == 0:
        return
    detail = (r.stderr or r.stdout or "").strip()
    low = detail.lower()
    if "1002" in detail or "not allowed to send keystrokes" in low or "assistive" in low:
        raise PageTurnError(
            "macOS blocked the page-turn key press. Allow the app running Voxtral "
            "Reader (Terminal or Python) in System Settings > Privacy & Security > "
            "Accessibility."
        )
    if "-1743" in detail or "not authorized" in low:
        raise PageTurnError(
            "macOS blocked control of Books/System Events. Allow it in System "
            "Settings > Privacy & Security > Automation."
        )
    raise PageTurnError(f"Could not turn the page: {detail or 'osascript failed'}")
