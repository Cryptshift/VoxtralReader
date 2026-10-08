import re
import fitz  # pymupdf
from typing import List, Dict, Any, Tuple

# ── Inline citation patterns ──────────────────────────────────────────────────
# [1], [1,2], [1-3], [1, 2, 3]
_CITE_NUM = re.compile(r'\[\d+(?:\s*[,\-]\s*\d+)*\]')
# [Smith et al., 2023], [Smith, 2023a]
_CITE_AUTHOR = re.compile(r'\[(?:[A-Z][a-z]+(?:\s+et\s+al\.?)?,?\s*)+\d{4}[a-z]?\]')
# [Smith2023], [SmithJones2020]
_CITE_KEY = re.compile(r'\[[A-Z][a-zA-Z]+\d{4}[a-z]?\]')
# (Smith et al., 2023), (Smith, 2023; Jones, 2020), (Smith & Jones, 2023)
_CITE_PAREN = re.compile(
    r'\((?:[A-Z][a-z]+(?:\s+(?:et\s+al\.?|&\s+[A-Z][a-z]+|and\s+[A-Z][a-z]+))?,?\s*\d{4}[a-z]?'
    r'(?:,\s*p(?:p)?\.\s*\d+[-–]\d+)?(?:;\s*)?)+\)'
)
# Superscript citation / footnote markers that text extraction glues onto the
# preceding word: "shown1,2 the", "results.12 We". Require at least three
# lowercase letters before the digits (optionally followed by . , ;) so that
# decimals ("0.01", "93.25"), versions ("Python 3.12") and symbols ("x2",
# "mp3", "H2O") are left alone.
_CITE_SUPER = re.compile(
    r'(?:(?<=[a-z]{3})|(?<=[a-z]{3}[.,;]))\d{1,2}(?:,\d{1,2})*(?=[\s,.;:)]|$)'
)

# ── Math ──────────────────────────────────────────────────────────────────────
_MATH_ENV = re.compile(
    r'\\begin\{(?:equation|align|eqnarray|array|matrix|gather|multline)[\*]?\}'
    r'[\s\S]*?'
    r'\\end\{(?:equation|align|eqnarray|array|matrix|gather|multline)[\*]?\}',
    re.IGNORECASE,
)
_MATH_DISPLAY = re.compile(r'\$\$[\s\S]*?\$\$')
_MATH_INLINE = re.compile(r'\$[^$\n]{1,120}\$')

# ── LaTeX / markup ────────────────────────────────────────────────────────────
_LATEX_CMD = re.compile(r'\\[a-zA-Z]+(?:\{[^}]*\})*')
_LATEX_BRACKET = re.compile(r'\{[^}]{0,60}\}')

# ── URLs / DOIs / emails ──────────────────────────────────────────────────────
_URL = re.compile(r'https?://\S+|www\.\S+|\bftp://\S+')
_DOI = re.compile(r'\bdoi:\s*\S+|\bDOI:\s*\S+|\b10\.\d{4,9}/\S+')
_EMAIL = re.compile(r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b')

# ── Structural noise ──────────────────────────────────────────────────────────
# Match ASCII hyphen AND Unicode hyphens/dashes (U+2010–U+2015)
_LINE_BREAK_HYPHEN = re.compile(r'(\w{2,})[‐‑‒–\-]\n(\w{2,})')
_STANDALONE_NUMBER = re.compile(r'^\s*\d+\s*$', re.MULTILINE)
_NOISE_LINE = re.compile(r'^\s*[^A-Za-z\n]{0,6}\s*$', re.MULTILINE)

# ── Abbreviation expansions (order matters — longer patterns first) ────────────
_ABBREVS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r'\bFigs?\.\s*(\d+)'), r'Figure \1'),
    (re.compile(r'\bEqs?\.\s*(\d+)'), r'Equation \1'),
    (re.compile(r'\bTabs?\.\s*(\d+)'), r'Table \1'),
    (re.compile(r'\bSecs?\.\s*(\d+)'), r'Section \1'),
    (re.compile(r'\bChaps?\.\s*(\d+)'), r'Chapter \1'),
    (re.compile(r'\bApp\.\s*([A-Z\d])'), r'Appendix \1'),
    (re.compile(r'\bvol\.\s*(\d)', re.I), r'volume \1'),
    (re.compile(r'\bpp\.\s*(\d)'), r'pages \1'),
    (re.compile(r'\bp\.\s*(\d)\b'), r'page \1'),
    (re.compile(r'\bno\.\s*(\d)', re.I), r'number \1'),
    (re.compile(r'\bvs\.\s'), 'versus '),
    (re.compile(r'\be\.g\.,?\s'), 'for example, '),
    (re.compile(r'\bi\.e\.,?\s'), 'that is, '),
    (re.compile(r'\bca\.\s'), 'approximately '),
    (re.compile(r'\bcf\.\s'), 'compare '),
    (re.compile(r'\bw\.r\.t\.\s'), 'with respect to '),
    (re.compile(r'\bw\.l\.o\.g\.\s'), 'without loss of generality, '),
    (re.compile(r'\bs\.t\.\s'), 'such that '),
    (re.compile(r'\bet\s+al\.\s'), 'et al '),
    (re.compile(r'\bapprox\.\s'), 'approximately '),
    (re.compile(r'\bprof\.\s', re.I), 'Professor '),
    (re.compile(r'\bdr\.\s', re.I), 'Doctor '),
    (re.compile(r'\bst\.\s', re.I), 'Saint '),
]

# ── Section heading detection ─────────────────────────────────────────────────
# Single line only ([ \t], not \s, so a match can't run into the next line).
_SECTION_NUMBERED = re.compile(
    r'^(?:\d{1,2}\.)*\d{1,2}\.?[ \t]+([A-Z][A-Za-z0-9 \t\-:,&/]{1,60})$', re.MULTILINE
)
_SECTION_ROMAN = re.compile(
    r'^[IVX]{1,5}\.[ \t]+([A-Z][A-Za-z \t\-:,&/]{1,60})$', re.MULTILINE
)
_SECTION_ALLCAPS = re.compile(
    r'^(ABSTRACT|INTRODUCTION|RELATED WORK|BACKGROUND|METHODS?|METHODOLOGY'
    r'|EXPERIMENTS?|EXPERIMENTAL SETUP|RESULTS?|EVALUATION|DISCUSSION'
    r'|CONCLUSION|FUTURE WORK|ACKNOWLEDGMENTS?|APPENDIX'
    r'|REFERENCES?|BIBLIOGRAPHY)$',
    re.MULTILINE,
)
_SECTION_HEADING_PREFIX = re.compile(r'^(?:\d+\.)*\d*\.?\s*|^[IVX]+\.\s*')

# Words that end sentences far more often than headings, and auxiliaries that
# show up in body text ("3 Patients were excluded due to") but rarely in titles.
_HEADING_BAD_LAST = {
    "a", "an", "the", "and", "or", "of", "to", "in", "on", "at", "by", "for",
    "from", "with", "as", "that", "which", "due", "than", "is", "are", "was", "were",
}
_HEADING_AUX = {"is", "are", "was", "were", "be", "been", "has", "have", "had"}
_HEADING_MINOR = {"a", "an", "the", "and", "or", "of", "to", "in", "on", "at", "by", "for", "from", "with", "vs"}

# Anything worth sending to TTS has at least two letters/digits in a row
# ("Yes.", "No.", "OK"); punctuation crumbs like "—" or "(a)" don't.
_SPEAKABLE = re.compile(r'[^\W_]{2,}')


def is_speakable(text: str) -> bool:
    return bool(_SPEAKABLE.search(text or ""))


def _looks_like_heading(title: str) -> bool:
    """Filter numbered/roman "headings" that are really wrapped body lines."""
    title = title.strip()
    words = title.split()
    if not 1 <= len(words) <= 8 or title[-1] in ",;":
        return False
    lower = [w.lower().strip(",:;") for w in words]
    if lower[-1] in _HEADING_BAD_LAST:
        return False
    if any(w in _HEADING_AUX for w, orig in zip(lower, words) if orig[:1].islower()):
        return False
    if len(words) > 4:
        # Long headings are Title Case; long sentence-case lines are body text.
        major = [w for w, l in zip(words, lower) if l not in _HEADING_MINOR]
        if sum(w[:1].isupper() for w in major) * 2 < len(major):
            return False
    return True

# References section boundary
_REFS_BOUNDARY = re.compile(
    r'\n(REFERENCES?|BIBLIOGRAPHY|WORKS CITED|LITERATURE CITED)\s*\n',
    re.IGNORECASE,
)


# ─────────────────────────────────────────────────────────────────────────────
# Core functions
# ─────────────────────────────────────────────────────────────────────────────

def _extract_page(page: fitz.Page) -> str:
    """
    Extract text using paragraph blocks rather than individual words.
    Each block is a complete paragraph — they never span columns — so
    left-column and right-column paragraphs stay separate.
    Reading order: full-width blocks in y-order; then left column top→bottom;
    then right column top→bottom (correct for academic paper layout).
    """
    raw = page.get_text("blocks")
    # block tuple: (x0, y0, x1, y1, text, block_no, block_type)
    blocks = [
        (b[0], b[1], b[2], b[3], b[4].strip())
        for b in raw
        if b[6] == 0 and b[4].strip()   # type 0 = text
    ]
    if not blocks:
        return ""

    page_w = page.rect.width
    mid    = page_w / 2

    full_w, left_col, right_col = [], [], []
    for b in blocks:
        bw = b[2] - b[0]
        cx = (b[0] + b[2]) / 2
        if bw > page_w * 0.55:      # spans most of the page → full-width
            full_w.append(b)
        elif cx < mid:
            left_col.append(b)
        else:
            right_col.append(b)

    full_w.sort(   key=lambda b: b[1])
    left_col.sort( key=lambda b: b[1])
    right_col.sort(key=lambda b: b[1])

    # No real two-column content → just sort everything by y
    if not right_col or not left_col:
        all_b = sorted(full_w + left_col + right_col, key=lambda b: b[1])
        return "\n\n".join(b[4] for b in all_b)

    # Interleave full-width blocks at their y-position, rest left→right
    parts: List[str] = []
    li = ri = 0
    for fb in full_w:
        fy = fb[1]
        while li < len(left_col)  and left_col[li][1]  < fy:
            parts.append(left_col[li][4]);  li += 1
        while ri < len(right_col) and right_col[ri][1] < fy:
            parts.append(right_col[ri][4]); ri += 1
        parts.append(fb[4])
    while li < len(left_col):
        parts.append(left_col[li][4]);  li += 1
    while ri < len(right_col):
        parts.append(right_col[ri][4]); ri += 1

    return "\n\n".join(parts)


def _extract_raw(path: str) -> str:
    pages = []
    doc = fitz.open(path)
    for page in doc:
        try:
            text = _extract_page(page)
            if text.strip():
                pages.append(text)
        except Exception:
            pass
    doc.close()
    return "\n\n".join(pages)


def _separate_references(text: str) -> Tuple[str, str]:
    # The real references heading is the last one, near the end of the text.
    # A "References" line near the start is a table-of-contents entry; treating
    # it as the boundary would throw away the whole document.
    matches = list(_REFS_BOUNDARY.finditer(text))
    if matches and matches[-1].start() >= len(text) * 0.2:
        m = matches[-1]
        return text[: m.start()], text[m.start() :]
    return text, ""


def _clean(text: str) -> str:
    # Fix line-break hyphenation before anything else
    text = _LINE_BREAK_HYPHEN.sub(r'\1\2', text)

    # Strip math
    text = _MATH_ENV.sub(' [equation] ', text)
    text = _MATH_DISPLAY.sub(' [equation] ', text)
    text = _MATH_INLINE.sub(' [equation] ', text)

    # Strip LaTeX
    text = _LATEX_CMD.sub('', text)
    text = _LATEX_BRACKET.sub('', text)

    # Strip all citation forms
    text = _CITE_NUM.sub('', text)
    text = _CITE_AUTHOR.sub('', text)
    text = _CITE_KEY.sub('', text)
    text = _CITE_PAREN.sub('', text)
    text = _CITE_SUPER.sub('', text)

    # Strip URLs, DOIs, emails
    text = _URL.sub('', text)
    text = _DOI.sub('', text)
    text = _EMAIL.sub('', text)

    # Expand abbreviations
    for pat, repl in _ABBREVS:
        text = pat.sub(repl, text)

    # Remove standalone page numbers and noise lines
    text = _STANDALONE_NUMBER.sub('', text)
    text = _NOISE_LINE.sub('', text)
    # Remove trailing bare numbers / ISBNs / ISSNs left after other cleanups
    text = re.sub(r'\s+\d{5,}[,.]?\s*$', '', text, flags=re.MULTILINE)

    # Normalize whitespace
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' +\n', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text.strip()


def _detect_sections(text: str) -> List[Tuple[int, str]]:
    found: Dict[int, str] = {}
    for pattern in (_SECTION_NUMBERED, _SECTION_ROMAN, _SECTION_ALLCAPS):
        for m in pattern.finditer(text):
            if pattern is not _SECTION_ALLCAPS and not _looks_like_heading(m.group(1)):
                continue
            pos = m.start()
            name = _SECTION_HEADING_PREFIX.sub('', m.group(0).strip()).strip()
            if name and pos not in found:
                found[pos] = name
    return sorted(found.items())


def _split_chunks(text: str, max_chars: int = 160) -> List[str]:
    sentences = re.split(r'(?<=[.!?])\s+', text)
    chunks: List[str] = []
    bucket: List[str] = []
    bucket_len = 0

    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue

        if len(sent) > max_chars:
            if bucket:
                chunks.append(' '.join(bucket))
                bucket, bucket_len = [], 0
            # Split at clause boundaries
            parts = re.split(r'(?<=[,;:])\s+', sent)
            sub: List[str] = []
            sub_len = 0
            for part in parts:
                if sub_len + len(part) + 1 > max_chars and sub:
                    chunks.append(' '.join(sub))
                    sub, sub_len = [part], len(part)
                else:
                    sub.append(part)
                    sub_len += len(part) + 1
            if sub:
                chunks.append(' '.join(sub))
            continue

        if bucket_len + len(sent) + 1 > max_chars and bucket:
            chunks.append(' '.join(bucket))
            bucket, bucket_len = [sent], len(sent)
        else:
            bucket.append(sent)
            bucket_len += len(sent) + 1

    if bucket:
        chunks.append(' '.join(bucket))

    return [c for c in chunks if is_speakable(c)]


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def process_pdf(path: str, include_references: bool = False) -> Dict[str, Any]:
    raw = _extract_raw(path)
    body_raw, refs_raw = _separate_references(raw)

    body = _clean(body_raw)
    refs = _clean(refs_raw) if refs_raw else ""

    sec_positions = _detect_sections(body)

    paragraphs = [p.strip() for p in re.split(r'\n\n+', body) if p.strip()]

    all_chunks: List[Dict[str, Any]] = []
    section_map: Dict[str, int] = {}
    current_section = "Document"
    sec_idx = 0
    char_pos = 0

    for para in paragraphs:
        while sec_idx < len(sec_positions):
            sp, sname = sec_positions[sec_idx]
            if sp <= char_pos + len(para):
                current_section = sname
                if sname not in section_map:
                    section_map[sname] = len(all_chunks)
                sec_idx += 1
            else:
                break

        for chunk_text in _split_chunks(para):
            all_chunks.append({
                "text": chunk_text,
                "section": current_section,
                "index": len(all_chunks),
            })

        char_pos += len(para) + 2

    if include_references and refs:
        section_map["References"] = len(all_chunks)
        all_chunks.append({
            "text": "--- References Section ---",
            "section": "References",
            "index": len(all_chunks),
        })
        for para in [p.strip() for p in re.split(r'\n\n+', refs) if p.strip()][1:]:
            for chunk_text in _split_chunks(para, max_chars=160):
                all_chunks.append({
                    "text": chunk_text,
                    "section": "References",
                    "index": len(all_chunks),
                })

    sections: List[Dict[str, Any]] = []
    if sec_positions:
        for _, name in sec_positions:
            if name in section_map:
                sections.append({"name": name, "chunk_idx": section_map[name]})
    if not sections:
        sections = [{"name": "Document", "chunk_idx": 0}]
    if include_references and refs and "References" in section_map:
        sections.append({"name": "References", "chunk_idx": section_map["References"]})

    return {
        "sections": sections,
        "chunks": all_chunks,
        "total_chunks": len(all_chunks),
        "has_references": bool(refs),
    }
