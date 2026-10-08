import fitz
import pytest

from pdf_processor import (
    _clean,
    _detect_sections,
    _separate_references,
    _split_chunks,
    is_speakable,
    process_pdf,
)


# ── Superscript citations vs. numbers that must survive ──────────────────────

@pytest.mark.parametrize("text, keep", [
    ("The learning rate was 0.01 and dropout 0.25 in all runs.", ["0.01", "0.25"]),
    ("Accuracy rose to 93.25 overall.", ["93.25"]),
    ("Significant at p < 0.05.", ["0.05"]),
    ("We used Python 3.12 for training.", ["3.12"]),
    ("We tested x2 and mp3 files and H2O samples.", ["x2", "mp3", "H2O"]),
    ("Temperature was 37.5 C.", ["37.5"]),
])
def test_clean_keeps_decimals_versions_and_symbols(text, keep):
    out = _clean(text)
    for token in keep:
        assert token in out


@pytest.mark.parametrize("text, expected", [
    ("as shown1,2 in prior work", "as shown in prior work"),
    ("improved the results.12 We then", "improved the results. We then"),
    ("is widely used3 in practice", "is widely used in practice"),
])
def test_clean_still_strips_superscript_citations(text, expected):
    assert _clean(text) == expected


# ── References boundary ──────────────────────────────────────────────────────

BODY = "\n\n".join(f"Paragraph {i} of the body text, long enough to matter." for i in range(30))


def test_toc_references_line_is_not_the_boundary():
    text = "Contents\nIntroduction\nReferences\nAppendix\n\n" + BODY
    body, refs = _separate_references(text)
    assert refs == ""
    assert "Paragraph 29" in body


def test_real_references_section_is_split_off():
    text = BODY + "\n\nReferences\n[1] A. Author. A paper. 2020.\n"
    body, refs = _separate_references(text)
    assert "Paragraph 29" in body
    assert refs.strip().startswith("References")
    assert "A. Author" not in body


def test_toc_and_real_references_splits_at_the_real_one():
    text = "Contents\nIntroduction\nReferences\n\n" + BODY + "\n\nREFERENCES\n[1] Someone. 2019.\n"
    body, refs = _separate_references(text)
    assert "Paragraph 0" in body and "Paragraph 29" in body
    assert "Someone" in refs and "Someone" not in body


# ── Section headings ─────────────────────────────────────────────────────────

def _names(text):
    return [name for _, name in _detect_sections(text)]


def test_numbered_body_line_is_not_a_heading():
    text = "Intro text.\n\n3 Patients were excluded due to\nmissing data in the trials.\n\nMore."
    assert _names(text) == []


@pytest.mark.parametrize("line, name", [
    ("1 Introduction", "Introduction"),
    ("2 Related Work", "Related Work"),
    ("3.1 Data collection and preprocessing", "Data collection and preprocessing"),
    ("4. Results", "Results"),
    ("IV. Experiments", "Experiments"),
    ("ABSTRACT", "ABSTRACT"),
])
def test_real_headings_are_detected(line, name):
    assert _names(f"Some text.\n\n{line}\n\nBody paragraph follows here.") == [name]


def test_heading_does_not_swallow_the_next_line():
    assert _names("2 Methods\nWe used data from two sites.") == ["Methods"]


# ── Short chunks ─────────────────────────────────────────────────────────────

def test_short_meaningful_sentences_are_kept():
    assert _split_chunks("No.") == ["No."]
    assert _split_chunks("Yes.") == ["Yes."]


def test_punctuation_crumbs_are_dropped():
    assert _split_chunks("—") == []
    assert not is_speakable("(a)")
    assert is_speakable("OK")


# ── End to end on a generated PDF ────────────────────────────────────────────

def _make_pdf(path, text):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_textbox(fitz.Rect(50, 50, 550, 800), text, fontsize=10)
    doc.save(str(path))
    doc.close()


def test_process_pdf_end_to_end(tmp_path):
    pdf = tmp_path / "paper.pdf"
    _make_pdf(pdf, (
        "1 Introduction\n\n"
        "The learning rate was 0.01 for all runs (Smith et al., 2020) [3].\n\n"
        "Yes.\n\n"
        "2 Methods\n\n"
        "We follow e.g. prior work on Python 3.12 pipelines."
    ))
    doc = process_pdf(str(pdf))
    texts = [c["text"] for c in doc["chunks"]]
    joined = " ".join(texts)
    assert [s["name"] for s in doc["sections"]] == ["Introduction", "Methods"]
    assert "0.01" in joined and "3.12" in joined
    assert "Smith" not in joined and "[3]" not in joined
    assert "for example," in joined
    assert "Yes." in texts
    assert doc["total_chunks"] == len(doc["chunks"])


def test_blank_pdf_yields_no_chunks(tmp_path):
    pdf = tmp_path / "blank.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(str(pdf))
    doc.close()
    assert process_pdf(str(pdf))["total_chunks"] == 0
