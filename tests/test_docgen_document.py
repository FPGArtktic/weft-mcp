# SPDX-License-Identifier: GPL-3.0-only
"""Tests for the document blocks and their two emitters."""

import pytest

from weft.docgen.document import (
    HTML,
    MARKDOWN,
    Bullets,
    Diagram,
    Heading,
    Paragraph,
    Table,
    render,
)

TREE = {
    "module": "top",
    "resolved": True,
    "instances": [{"name": "u_leaf", "line": 1, "module": "leaf", "resolved": True}],
}


def test_a_heading_carries_its_level():
    assert render([Heading(2, "Ports")], MARKDOWN).strip() == "## Ports"
    assert render([Heading(2, "Ports")], HTML).count("<h2>Ports</h2>") == 1


def test_a_heading_below_html_cannot_go_deeper_than_six():
    assert "<h6>" in render([Heading(9, "deep")], HTML)


def test_a_table_gets_its_rule():
    got = render([Table(["A", "B"], [["1", "2"]])], MARKDOWN)
    assert got.splitlines() == ["| A | B |", "|---|---|", "| 1 | 2 |"]


def test_an_empty_cell_reads_as_a_dash():
    """A blank cell in a reference table is ambiguous; a dash is not."""
    assert "| - |" in render([Table(["A"], [[""]])], MARKDOWN)


def test_a_pipe_in_a_cell_does_not_end_the_column():
    got = render([Table(["A"], [["a|b"]])], MARKDOWN)
    assert "a\\|b" in got


def test_html_escapes_what_would_otherwise_be_markup():
    got = render([Paragraph("<script>alert(1)</script>")], HTML)
    assert "<script>" not in got
    assert "&lt;script&gt;" in got


def test_a_diagram_is_mermaid_in_markdown():
    """GitHub and the Markdown viewers draw a mermaid fence."""
    got = render([Diagram(TREE)], MARKDOWN)
    assert "```mermaid" in got
    assert "graph TD" in got


def test_a_diagram_is_svg_in_html():
    """A browser draws no mermaid, and an offline page cannot fetch a renderer."""
    got = render([Diagram(TREE)], HTML)
    assert "<svg" in got
    assert "graph TD" not in got


def test_the_svg_diagram_needs_nothing_to_display():
    """The only URL in it is the SVG namespace, which is a name, not a fetch."""
    got = render([Diagram(TREE)], HTML)
    assert "<script" not in got
    assert "src=" not in got
    assert "href=" not in got


def test_a_property_table_has_no_empty_header_strip():
    """A header row of blank cells reads as a rendering fault."""
    got = render([Table(["", ""], [["Device", "10M04"]])], HTML)
    assert "<thead>" not in got
    assert 'class="plain"' in got


def test_a_titled_table_keeps_its_header():
    assert "<thead>" in render([Table(["Signal", "Pin"], [["clk", "28"]])], HTML)


def test_bullets_render_in_both_forms():
    assert render([Bullets(["one", "two"])], MARKDOWN).strip() == "- one\n- two"
    assert "<li>one</li>" in render([Bullets(["one"])], HTML)


def test_the_html_page_carries_the_title():
    assert "<title>counter</title>" in render([], HTML, title="counter")


def test_the_html_page_fetches_nothing():
    """An offline server must not emit a page that needs the network."""
    got = render([Heading(1, "x")], HTML, title="x")
    assert "http://" not in got and "https://" not in got
    assert "<script" not in got


def test_an_unknown_format_is_refused():
    with pytest.raises(ValueError, match="unknown format"):
        render([], "pdf")
