# SPDX-License-Identifier: GPL-3.0-only
"""Reading the kernel-doc header a module carries above itself.

The convention is the kernel's, adapted to HDL: a comment block opening with
`name - one-line summary`, then `@field: text` for each port and parameter,
then prose. Both comment syntaxes are read, `/** */` for Verilog and `--` for
VHDL, because the demonstration project uses both and a reader should not have
to care which.

The block is found by the name it announces rather than by sitting next to the
declaration. Nothing useful sits between them in principle, but in practice a
`timescale` or a `library` clause does, and a file may declare more than one
module. Searching for the name is exact where adjacency is a guess.
"""

import re
from dataclasses import dataclass, field, replace

from .model import Module

#: The line that opens a header: the module's own name, then its summary.
_OPENING = r"^{name}\s+[-–]\s+(?P<summary>\S.*)$"

#: A documented field: "@clk: clock, rising edge active".
_FIELD = re.compile(r"^@(?P<name>[A-Za-z_][\w$]*)\s*:\s*(?P<text>.*)$")

#: What starts a comment line, once the line has been stripped.
_MARKERS = ("--", "//", "*/", "/**", "/*", "*")


@dataclass(frozen=True)
class Header:
    """A module's documentation, as its author wrote it.

    @summary: the one line after the name, never invented
    @description: the prose below the field list, blank lines preserved as
                  paragraph breaks
    @fields: text for each @name, keyed by the name as the header spells it
    """

    summary: str
    description: str | None = None
    fields: dict[str, str] = field(default_factory=dict)


def parse(text: str, name: str) -> Header | None:
    """parse - the header block announcing @name

    @text: the whole source file
    @name: the module or entity the header should be about

    Return: the Header, or None when the file carries no such block. A module
    without one is normal and not an error; documentation then has facts from
    the AST and no prose, which is the correct outcome rather than a guess.
    """
    lines = text.splitlines()
    opening = re.compile(_OPENING.format(name=re.escape(name)), re.IGNORECASE)

    for number, line in enumerate(lines):
        stripped = _uncomment(line)
        if stripped is None:
            continue
        found = opening.match(stripped)
        if found:
            return _read(lines, number, found["summary"].strip())
    return None


def _read(lines: list[str], start: int, summary: str) -> Header:
    """_read - the fields and prose below the opening line."""
    fields: dict[str, str] = {}
    body: list[str] = []
    current: str | None = None

    for line in lines[start + 1 :]:
        stripped = _uncomment(line)
        if stripped is None:
            break

        found = _FIELD.match(stripped)
        if found:
            current = found["name"]
            fields[current] = found["text"].strip()
            continue

        # An indented line below a field continues it; anything else has left
        # the field list behind and belongs to the prose.
        if current and stripped and line.lstrip().startswith(("*", "--", "//")) is False:
            fields[current] = f"{fields[current]} {stripped}".strip()
            continue
        if current and stripped and _indented(line):
            fields[current] = f"{fields[current]} {stripped}".strip()
            continue

        current = None
        body.append(stripped)

    return Header(summary=summary, description=_prose(body), fields=fields)


def _uncomment(line: str) -> str | None:
    """_uncomment - a comment line without its marker

    Return: the content, empty for a bare marker, or None when the line is not
    a comment at all -- which is where a header block ends.
    """
    stripped = line.strip()
    if not stripped:
        return None
    for marker in _MARKERS:
        if stripped.startswith(marker):
            rest = stripped[len(marker) :]
            if marker == "*" and rest[:1] not in ("", " ", "\t"):
                # A line opening with `*foo` is not a continuation marker.
                continue
            return rest.strip().removesuffix("*/").strip()
    return None


def _indented(line: str) -> bool:
    """_indented - whether a comment line is indented past its marker."""
    body = line.strip()
    for marker in _MARKERS:
        if body.startswith(marker):
            return body[len(marker) :].startswith(("  ", "\t"))
    return False


def _prose(body: list[str]) -> str | None:
    """_prose - the description, with paragraph breaks kept and edges trimmed.

    Lines are joined inside a paragraph. A header wraps its prose to fit a
    comment block, and those line breaks mean nothing to a reader of the
    rendered document.
    """
    paragraphs: list[str] = []
    current: list[str] = []
    for line in body:
        if line:
            current.append(line)
        elif current:
            paragraphs.append(" ".join(current))
            current = []
    if current:
        paragraphs.append(" ".join(current))
    return "\n\n".join(paragraphs) or None


def attach(module: Module, text: str) -> Module:
    """attach - fill a module's documentation from its own header

    @module: as the parser produced it
    @text: the source file the module was found in

    Ports and parameters are matched to header fields without regard to case,
    because GHDL folds VHDL identifiers and the header is written by hand.

    Return: the module with whatever the header said, or unchanged when it has
    no header. A module without one is documented from the AST alone, which is
    the honest outcome: the facts are still right and no prose is invented.
    """
    found = parse(text, module.name)
    if found is None:
        return module

    fields = {name.lower(): value for name, value in found.fields.items()}
    return replace(
        module,
        summary=found.summary,
        description=found.description,
        ports=[replace(p, doc=fields.get(p.name.lower()) or None) for p in module.ports],
        parameters=[replace(p, doc=fields.get(p.name.lower()) or None) for p in module.parameters],
    )
