# SPDX-License-Identifier: GPL-3.0-only
"""Cutting a document into pieces worth retrieving.

Chunks follow the document's own structure rather than a fixed character
count. In a standard that structure is the clause numbering, and a citation
naming a clause is worth more than one naming a page: "1800-2017 §8.26.5" can
be looked up by a reader, "page 200" cannot be checked against another edition.

Running heads are removed first. They repeat on every page, so leaving them in
would put the document's title into every chunk and let a search for the title
match everywhere.
"""

import re
from dataclasses import dataclass

from .ingest import Page

#: A heading looks like "8.26.5 Casting and object reference assignment": a
#: dotted number, then a title, alone on its line.
_HEADING = re.compile(r"^(?P<clause>\d+(?:\.\d+){0,5})\.?\s+(?P<title>\S.{2,90})$")

#: "IEEE Std 1800-2017" in a running head gives the citation its designation.
_DESIGNATION = re.compile(r"\b(?:IEEE|IEC|ISO)\s+Std\s+(?P<number>[\w.-]+)")

#: A Markdown heading: one to six hashes, then the title.
_MARKDOWN_HEADING = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<title>\S.*)$")

#: A line appearing on at least this share of pages is furniture, not content.
RUNNING_SHARE = 0.5

#: Below this many pages, repetition proves nothing.
MIN_PAGES_FOR_RUNNING = 4

DEFAULT_MAX_CHARS = 1500
DEFAULT_OVERLAP = 150


@dataclass(frozen=True)
class Chunk:
    """One retrievable piece of a document.

    @text: the content
    @page: page the piece starts on
    @clause: clause number, when the document is numbered that way
    @heading: the heading the piece falls under
    @ordinal: position in the document, for stable ordering
    """

    text: str
    page: int
    clause: str | None
    heading: str | None
    ordinal: int


def running_lines(pages: list[Page]) -> set[str]:
    """running_lines - the lines that are page furniture

    @pages: the document

    Return: lines appearing on at least RUNNING_SHARE of the pages. A short
    document returns none: on four pages, a line appearing on two is as likely
    to be a repeated sentence as a running head.
    """
    if len(pages) < MIN_PAGES_FOR_RUNNING:
        return set()

    counts: dict[str, int] = {}
    for page in pages:
        for line in {stripped for line in page.text.splitlines() if (stripped := line.strip())}:
            counts[line] = counts.get(line, 0) + 1

    threshold = max(2, int(len(pages) * RUNNING_SHARE))
    return {line for line, count in counts.items() if count >= threshold}


def designation(pages: list[Page]) -> str | None:
    """designation - the standard's number, if this is a standard

    Taken from the running heads, where a standard prints its own designation
    on every page. Without one the citation falls back to the file name.
    """
    for line in running_lines(pages):
        m = _DESIGNATION.search(line)
        if m:
            return m["number"]
    return None


def chunks(
    pages: list[Page],
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap: int = DEFAULT_OVERLAP,
) -> list[Chunk]:
    """chunks - cut a document at its own headings

    @pages: the document, in order
    @max_chars: longest chunk before a clause is split further
    @overlap: characters repeated at a split, so a sentence cut in half is
              still findable from either side
    @Return: the chunks, in document order.

    A clause shorter than @max_chars stays whole, which is the point: retrieval
    then returns the clause, not a window that happens to straddle two.
    """
    furniture = running_lines(pages)

    pieces: list[Chunk] = []
    clause: str | None = None
    heading: str | None = None
    start_page = pages[0].number if pages else 1
    buffer: list[str] = []

    def flush() -> None:
        text = "\n".join(buffer).strip()
        buffer.clear()
        if not text:
            return
        for part in _split(text, max_chars, overlap):
            pieces.append(
                Chunk(
                    text=part,
                    page=start_page,
                    clause=clause,
                    heading=heading,
                    ordinal=len(pieces),
                )
            )

    for page in pages:
        for line in page.text.splitlines():
            stripped = line.strip()
            if not stripped or stripped in furniture:
                continue

            found = _HEADING.match(stripped)
            if found and _is_title(found["title"]) and _accepts(found["clause"], clause):
                flush()
                clause, heading = found["clause"], found["title"]
                start_page = page.number
                buffer.append(stripped)
                continue

            if not buffer:
                start_page = page.number
            buffer.append(stripped)

    flush()
    return pieces


def _is_title(title: str) -> bool:
    """_is_title - whether the text after the number reads as a heading

    A heading is a title, not the start of a sentence: it opens with a capital
    or a code identifier and does not trail off into punctuation.
    """
    if title[-1] in ",;:)(":
        return False
    return (
        title[0].isupper()
        or title[0] == "$"
        or title[0] == "`"
        or title[0].islower()
        and "_" in title.split()[0]
    )


def _accepts(candidate: str, previous: str | None) -> bool:
    """_accepts - whether a numbered line continues the clause numbering

    The shape "number, then a title" catches far more than headings. A
    standard's front matter carries the publisher's address, so "445 Hoes
    Lane" parses as clause 445, and prose refers to its own clauses, so "see
    16.14" parses as a heading. Checking only the leading number lets both
    through.

    Numbering runs forwards and does not leap: a heading is greater than the
    one before it, and its top-level clause is the current one or the next.
    That rules out the address, which leaps, and the backward reference, which
    goes the wrong way. Forward references are ruled out by the blank line a
    heading stands after, which prose does not have.

    Demanding the exact successor would be stricter but worse: one heading
    lost to a page break would desynchronise the rest of the document, and
    every later heading would then be rejected for following the wrong one.
    """
    try:
        number = tuple(int(part) for part in candidate.split("."))
    except ValueError:
        return False

    if previous is None:
        # Anchor inside clause 1; anything numbered before it is furniture.
        return number[0] == 1

    current = tuple(int(part) for part in previous.split("."))
    return number > current and number[0] <= current[0] + 1


def _split(text: str, max_chars: int, overlap: int) -> list[str]:
    """_split - break a run of text that is too long to retrieve whole

    Splits on a blank-line boundary where one is near enough, so a paragraph
    is not cut mid-sentence when it does not have to be.
    """
    if len(text) <= max_chars:
        return [text]

    parts = []
    position = 0
    while position < len(text):
        end = min(position + max_chars, len(text))
        if end < len(text):
            boundary = text.rfind("\n", position + max_chars // 2, end)
            if boundary > position:
                end = boundary
        parts.append(text[position:end].strip())
        if end >= len(text):
            break
        position = max(end - overlap, position + 1)
    return [p for p in parts if p]


def markdown_chunks(
    text: str,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap: int = DEFAULT_OVERLAP,
) -> list[Chunk]:
    """markdown_chunks - cut generated documentation at its headings

    @text: the Markdown document
    @max_chars / @overlap: as in chunks()

    Generated documents have headings but no clause numbers, so a retrieved
    piece cites its section rather than a clause. Everything else is the same
    cut: a section shorter than @max_chars stays whole, because a port table
    split down the middle answers nothing.

    Return: the chunks, in document order.
    """
    pieces: list[Chunk] = []
    heading: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        body = "\n".join(buffer).strip()
        buffer.clear()
        if not body:
            return
        for part in _split(body, max_chars, overlap):
            pieces.append(
                Chunk(text=part, page=1, clause=None, heading=heading, ordinal=len(pieces))
            )

    for line in text.splitlines():
        found = _MARKDOWN_HEADING.match(line)
        if found:
            flush()
            heading = found["title"].strip()
            buffer.append(line)
            continue
        buffer.append(line)

    flush()
    return pieces
