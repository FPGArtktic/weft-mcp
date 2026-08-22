# SPDX-License-Identifier: GPL-3.0-only
"""A document as blocks, and the two ways of writing them out.

Markdown and HTML are both asked for, and generating one from the other would
mean carrying a Markdown parser to do it. Building both from the same blocks
is less code and cannot drift: a table that gains a column gains it in both.
"""

import html as escaping
from dataclasses import dataclass, field

#: Wrapper for the whole HTML document. No stylesheet is fetched and no script
#: runs: an offline server must not emit a page that needs the network to be
#: readable.
_HTML_PAGE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
body {{ font-family: system-ui, sans-serif; line-height: 1.5; margin: 2rem auto;
        max-width: 60rem; padding: 0 1rem; }}
table {{ border-collapse: collapse; margin: 1rem 0; display: block;
         overflow-x: auto; }}
th, td {{ border: 1px solid #999; padding: 0.3rem 0.6rem; text-align: left; }}
th {{ background: #f0f0f0; }}
code, pre {{ font-family: ui-monospace, monospace; }}
pre {{ background: #f6f6f6; padding: 0.8rem; overflow-x: auto; }}
</style>
</head>
<body>
{body}
</body>
</html>
"""


@dataclass(frozen=True)
class Heading:
    """A section title. @level is 1 for the document title."""

    level: int
    text: str


@dataclass(frozen=True)
class Paragraph:
    """A run of prose."""

    text: str


@dataclass(frozen=True)
class Table:
    """A table. @rows are already strings; empty cells are written as "-"."""

    headers: list[str]
    rows: list[list[str]] = field(default_factory=list)


@dataclass(frozen=True)
class Code:
    """A fenced block. @language "mermaid" is what makes a diagram render."""

    text: str
    language: str = ""


@dataclass(frozen=True)
class Bullets:
    """An unordered list."""

    items: list[str]


Block = Heading | Paragraph | Table | Code | Bullets

MARKDOWN = "markdown"
HTML = "html"


def render(blocks: list[Block], form: str, title: str = "") -> str:
    """render - write blocks out in one format

    @blocks: the document
    @form: "markdown" or "html"
    @title: page title, used by the HTML head

    Return: the document as text.

    Raises ValueError for an unknown format.
    """
    if form == MARKDOWN:
        return "\n\n".join(_markdown(b) for b in blocks) + "\n"
    if form == HTML:
        body = "\n".join(_html(b) for b in blocks)
        return _HTML_PAGE.format(title=escaping.escape(title or "Documentation"), body=body)
    raise ValueError(f"unknown format: {form}; expected {MARKDOWN} or {HTML}")


def _markdown(block: Block) -> str:
    """_markdown - one block as Markdown."""
    if isinstance(block, Heading):
        return f"{'#' * block.level} {block.text}"
    if isinstance(block, Paragraph):
        return block.text
    if isinstance(block, Code):
        return f"```{block.language}\n{block.text}\n```"
    if isinstance(block, Bullets):
        return "\n".join(f"- {item}" for item in block.items)

    head = "| " + " | ".join(block.headers) + " |"
    rule = "|" + "|".join("---" for _ in block.headers) + "|"
    body = ["| " + " | ".join(_cell(c) for c in row) + " |" for row in block.rows]
    return "\n".join([head, rule, *body])


def _html(block: Block) -> str:
    """_html - one block as HTML."""
    e = escaping.escape
    if isinstance(block, Heading):
        level = min(block.level, 6)
        return f"<h{level}>{e(block.text)}</h{level}>"
    if isinstance(block, Paragraph):
        return f"<p>{e(block.text)}</p>"
    if isinstance(block, Code):
        # A mermaid block is marked rather than escaped into a plain listing:
        # a viewer that draws diagrams draws it, and one that does not shows
        # the source, which is still the hierarchy in readable form.
        cls = ' class="mermaid"' if block.language == "mermaid" else ""
        return f"<pre{cls}>{e(block.text)}</pre>"
    if isinstance(block, Bullets):
        items = "".join(f"<li>{e(i)}</li>" for i in block.items)
        return f"<ul>{items}</ul>"

    head = "".join(f"<th>{e(h)}</th>" for h in block.headers)
    rows = "".join(
        "<tr>" + "".join(f"<td>{e(_cell(c))}</td>" for c in row) + "</tr>" for row in block.rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>"


def _cell(value: str) -> str:
    """_cell - an empty cell reads as a dash, and a pipe would end the column."""
    text = (value or "").strip()
    return text.replace("|", "\\|") if text else "-"
