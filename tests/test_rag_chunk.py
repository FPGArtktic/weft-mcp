# SPDX-License-Identifier: GPL-3.0-only
"""Tests for cutting a document into retrievable pieces.

The pages here are written by hand rather than taken from a real standard: the
corpus is copyrighted and stays out of the repository entirely.
"""

from weft.rag.chunk import Chunk, chunks, designation, markdown_chunks, running_lines
from weft.rag.ingest import Page

HEAD = "IEEE Std 1800-2017"
TITLE = "IEEE Standard for SystemVerilog"


def page(number, body):
    """page - a page carrying the running heads a standard prints."""
    return Page(number=number, text=f"{HEAD}\n{TITLE}\n\n{body}\n", ocr=False)


DOCUMENT = [
    page(1, "445 Hoes Lane\nPiscataway, NJ 08854, USA"),
    page(2, "1 Overview\nThis standard describes things.\n\n1.1 Scope\nThe scope is wide."),
    page(3, "1.2 Purpose\nThe purpose is narrow.\n\n2 Normative references\nSee 1.1 and 1.2."),
    page(
        4,
        "3 Procedural blocks\nBlocks contain statements.\n\n3.2.2.4 Sequential logic"
        " always_ff procedure\nThe always_ff procedure models it.",
    ),
]


def test_running_heads_are_removed():
    """Left in, the document's title would be in every chunk and a search for
    it would match everywhere."""
    furniture = running_lines(DOCUMENT)
    assert HEAD in furniture
    assert TITLE in furniture
    assert not any(HEAD in c.text for c in chunks(DOCUMENT))


def test_a_short_document_keeps_its_repeats():
    """On three pages, a line appearing twice is as likely to be a sentence."""
    assert running_lines(DOCUMENT[:3]) == set()


def test_designation_comes_from_the_running_head():
    assert designation(DOCUMENT) == "1800-2017"


def test_designation_is_absent_when_there_is_none():
    plain = [Page(number=i, text=f"page {i} body text here\n", ocr=False) for i in range(1, 8)]
    assert designation(plain) is None


def test_clauses_are_detected():
    found = {c.clause: c.heading for c in chunks(DOCUMENT) if c.clause}
    assert found["1"] == "Overview"
    assert found["1.1"] == "Scope"
    assert found["3.2.2.4"] == "Sequential logic always_ff procedure"


def test_a_street_address_is_not_clause_445():
    """The number-then-title shape matches the publisher's address perfectly
    well; only the numbering sequence rules it out."""
    assert all(c.clause != "445" for c in chunks(DOCUMENT))


def test_a_backward_reference_is_not_a_heading():
    """Prose refers to earlier clauses; numbering only runs forwards."""
    found = [c.clause for c in chunks(DOCUMENT) if c.clause]
    assert found.count("1.1") == 1


def test_a_skipped_heading_does_not_desynchronise():
    """One heading lost to a page break must not reject every later one.
    Demanding the exact successor would do exactly that: 1.4 does not follow
    1.1, and 2.3 follows neither."""
    gappy = [
        page(1, "1 Overview\nText."),
        page(2, "1.1 Scope\nText."),
        page(3, "1.4 Special terms\nText."),
        page(4, "2.3 Structures\nText."),
    ]
    found = [c.clause for c in chunks(gappy) if c.clause]
    assert found == ["1", "1.1", "1.4", "2.3"]


def test_a_leap_between_top_level_clauses_is_refused():
    """A known limit, recorded rather than hidden: the top-level number may
    advance by one. A document whose clause 2 to 6 headings were all lost to
    extraction resumes only at 2, and the pages between carry clause 1. That
    is the price of rejecting a street address, and on the standard this was
    built against it costs nothing -- all 41 clauses are found."""
    leaping = [
        page(1, "1 Overview\nText."),
        page(2, "7 Aggregate data types\nText."),
    ]
    assert [c.clause for c in chunks(leaping) if c.clause] == ["1"]


def test_a_clause_shorter_than_the_limit_stays_whole():
    """Retrieval should return the clause, not a window straddling two."""
    got = [c for c in chunks(DOCUMENT) if c.clause == "1.1"]
    assert len(got) == 1
    assert "The scope is wide." in got[0].text


def test_a_long_clause_is_split_but_keeps_its_clause():
    body = "1.9 Long clause\n" + "\n".join(f"Sentence number {i}." * 4 for i in range(200))
    got = [c for c in chunks([page(1, "1 Overview\nx"), page(2, body)]) if c.clause == "1.9"]
    assert len(got) > 1
    assert all(c.heading == "Long clause" for c in got)


def test_chunks_are_ordered():
    got = chunks(DOCUMENT)
    assert [c.ordinal for c in got] == list(range(len(got)))
    assert isinstance(got[0], Chunk)


def test_an_empty_document_yields_nothing():
    assert chunks([]) == []


def test_markdown_is_cut_at_its_headings():
    text = "# Title\n\nintro\n\n## Ports\n\n| a | b |\n\n## Clocks\n\nfifty megahertz\n"
    pieces = markdown_chunks(text)
    assert [c.heading for c in pieces] == ["Title", "Ports", "Clocks"]


def test_a_generated_chunk_carries_no_clause():
    """Generated documents have sections, not numbered clauses."""
    pieces = markdown_chunks("# Title\n\nbody\n")
    assert pieces[0].clause is None


def test_a_markdown_section_shorter_than_the_limit_stays_whole():
    text = "## Ports\n\n| clk | in |\n| rst_n | in |\n"
    assert len(markdown_chunks(text)) == 1


def test_a_long_markdown_section_is_split():
    text = "## Ports\n\n" + "\n".join(f"| sig{i} | in |" for i in range(400))
    assert len(markdown_chunks(text, max_chars=500)) > 1


def test_ordinals_run_from_zero_without_gaps():
    """The store indexes vectors by ordinal, so they must be positions."""
    pieces = markdown_chunks("# A\n\nx\n\n# B\n\ny\n\n# C\n\nz\n")
    assert [c.ordinal for c in pieces] == list(range(len(pieces)))


def test_text_that_is_not_a_heading_is_not_one():
    pieces = markdown_chunks("# Title\n\na line with a # inside it\n")
    assert len(pieces) == 1
