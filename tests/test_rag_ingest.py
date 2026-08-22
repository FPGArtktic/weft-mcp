# SPDX-License-Identifier: GPL-3.0-only
"""Tests for getting text out of a PDF.

The container boundary is mocked. A real PDF fixture would have to live in the
repository, and the corpus this was built against is copyrighted; the real
thing is exercised by tests/manual/index_pdf.py.
"""

import pytest

from weft import podman
from weft.rag import ingest as ingestmod
from weft.rag.ingest import IngestError, extract


@pytest.fixture
def canned(monkeypatch):
    """canned - answer each container run from a queue of transcripts."""
    calls = []

    def install(*outputs):
        queued = list(outputs)

        def fake_run(image, workspace, argv, timeout=None):
            calls.append(argv)
            code, text = queued.pop(0)
            return podman.Result(code, text)

        monkeypatch.setattr(ingestmod.podman, "run", fake_run)
        return calls

    return install


def test_pages_are_split_on_the_form_feed(image, tmp_path, canned):
    canned((0, "first page of real prose\fsecond page of real prose\fthird page of real prose\f"))
    got = extract(image, tmp_path, "doc.pdf")
    assert [p.number for p in got] == [1, 2, 3]
    assert got[1].text.strip() == "second page of real prose"
    assert not any(p.ocr for p in got)


def test_the_trailing_form_feed_is_not_a_page(tmp_path, image, canned):
    """pdftotext ends the last page with one too."""
    canned((0, "the only page, with enough prose on it\f"))
    assert len(extract(image, tmp_path, "doc.pdf")) == 1


def test_a_page_without_a_text_layer_is_recognised(image, tmp_path, canned):
    """Mixed documents are ordinary -- a scanned figure in a digital
    standard -- so the decision is per page, not per document."""
    calls = canned(
        (
            0,
            "real text on the first page of this document\f\f"
            "more real text on the third page here\f",
        ),
        (0, "===weft-page=== 2\nrecovered by ocr\n"),
    )
    got = extract(image, tmp_path, "doc.pdf")
    assert [p.ocr for p in got] == [False, True, False]
    assert got[1].text.strip() == "recovered by ocr"
    assert "tesseract" in " ".join(calls[1])


def test_no_ocr_run_when_every_page_has_text(image, tmp_path, canned):
    calls = canned((0, "a page with enough text on it\fand a second page with just as much text\f"))
    extract(image, tmp_path, "doc.pdf")
    assert len(calls) == 1


def test_the_ocr_language_is_passed_through(image, tmp_path, canned):
    calls = canned((0, "\f"), (0, "===weft-page=== 1\nsomething\n"))
    extract(image, tmp_path, "doc.pdf", language="pol")
    assert "-l pol" in " ".join(calls[1])


def test_a_page_ocr_could_not_read_keeps_its_place(image, tmp_path, canned):
    """A page that comes back empty is still a page; dropping it would shift
    every later page number."""
    canned(
        (0, "a first page with plenty of text\f\fa third page with plenty too\f"),
        (0, "===weft-page=== 2\n\n"),
    )
    got = extract(image, tmp_path, "doc.pdf")
    assert [p.number for p in got] == [1, 2, 3]


def test_an_unreadable_document_is_reported(image, tmp_path, canned):
    canned((1, "Syntax Error: Couldn't read xref table"))
    with pytest.raises(IngestError, match="cannot read"):
        extract(image, tmp_path, "doc.pdf")


def test_an_empty_document_is_reported(image, tmp_path, canned):
    canned((0, ""))
    with pytest.raises(IngestError, match="no pages"):
        extract(image, tmp_path, "doc.pdf")
