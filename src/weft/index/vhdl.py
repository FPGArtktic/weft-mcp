# SPDX-License-Identifier: GPL-2.0-only
"""Extracting VHDL structure from GHDL's XML dump.

`ghdl --file-to-xml` does more than parse: it analyses, so an entity
instantiated directly has to be in the library already. Indexing therefore
analyses every VHDL file into one scratch library first and dumps afterwards.

The dump carries the whole standard and ieee hierarchy alongside the file
asked for, so elements are filtered by their `file` attribute. Types come from
the source rather than from the XML: a subtype indication there is a reference
into that hierarchy, and resolving it would rebuild a declaration the source
already states plainly.
"""

import re
import xml.etree.ElementTree as ET

from .model import IN, INOUT, OUT, VHDL, Instance, Module, Parameter, Port

#: VHDL's modes, mapped onto the three this model keeps. A buffer port is an
#: output that the architecture may also read; callers care that it is driven.
MODES = {"in": IN, "out": OUT, "inout": INOUT, "buffer": OUT}

#: Mode keywords that may stand between the colon and the type.
_MODE_WORD = re.compile(r"\s*(in|out|inout|buffer)\b", re.I)


class ParseError(ValueError):
    """GHDL's dump cannot be read as an AST."""


def modules(payload: str, path: str, source: str) -> list[Module]:
    """modules - every entity declared in one file

    @payload: the output of `ghdl --file-to-xml`
    @path: the file as it was given to GHDL, used to filter the dump
    @source: that file's text, for the type declarations

    Return: one Module per entity, carrying the instances of whichever
    architecture belongs to it.

    Raises ParseError if the payload is not GHDL's XML.
    """
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as e:
        raise ParseError(f"ghdl did not return XML: {e}") from e

    starts = _line_starts(source)
    ours = [e for e in root.iter() if (e.get("file") or "").endswith(path)]

    architectures = [e for e in ours if e.get("kind") == "architecture_body"]
    found = []
    for entity in (e for e in ours if e.get("kind") == "entity_declaration"):
        name = entity.get("identifier") or "?"
        found.append(
            Module(
                name=name,
                language=VHDL,
                file=path,
                line=int(entity.get("line") or 1),
                ports=_ports(entity, source, starts),
                parameters=_generics(entity, source, starts),
                instances=_instances(architectures, name),
            )
        )
    return found


def _ports(entity: ET.Element, source: str, starts: list[int]) -> list[Port]:
    """_ports - the entity's port clause."""
    ports = []
    for node in entity.iter():
        if node.get("kind") != "interface_signal_declaration":
            continue
        mode = MODES.get((node.get("mode") or "in").lower())
        name = node.get("identifier")
        if mode is None or name is None:
            continue
        declared = _declared(source, starts, node)
        ports.append(Port(name=name, direction=mode, type=declared.get("type", "")))
    return ports


def _generics(entity: ET.Element, source: str, starts: list[int]) -> list[Parameter]:
    """_generics - the entity's generic clause, which is VHDL's parameters."""
    generics = []
    for node in entity.iter():
        if node.get("kind") != "interface_constant_declaration":
            continue
        name = node.get("identifier")
        if name is None:
            continue
        declared = _declared(source, starts, node)
        generics.append(
            Parameter(
                name=name,
                default=declared.get("default") or None,
                type=declared.get("type") or None,
            )
        )
    return generics


def _instances(architectures: list[ET.Element], entity: str) -> list[Instance]:
    """_instances - instantiations in the architectures belonging to @entity.

    An architecture names the entity it implements, so a file holding several
    entities does not hand one of them another's instances.
    """
    found = []
    for architecture in architectures:
        named = architecture.find("entity_name")
        if named is None or (named.get("identifier") or "").lower() != entity.lower():
            continue
        for node in architecture.iter():
            if node.get("kind") != "component_instantiation_statement":
                continue
            label = node.get("label")
            of = _instantiated(node)
            if label and of:
                found.append(Instance(name=label, of=of, line=int(node.get("line") or 1)))
    return found


def _instantiated(node: ET.Element) -> str | None:
    """_instantiated - the entity or component name behind an instantiation."""
    unit = node.find("instantiated_unit")
    if unit is None:
        return None
    for child in unit.iter():
        identifier = child.get("identifier")
        if identifier:
            return identifier
    return None


def _line_starts(source: str) -> list[int]:
    """_line_starts - byte offset of the first character of each line."""
    offsets, position = [0], 0
    for line in source.splitlines(keepends=True):
        position += len(line)
        offsets.append(position)
    return offsets


def _declared(source: str, starts: list[int], node: ET.Element) -> dict[str, str]:
    """_declared - the type and default as the source writes them

    GHDL says where the declaration begins and which way the port points; the
    text is read from there. Scanning rather than matching a pattern, because
    the type may carry its own parentheses -- std_logic_vector(3 downto 0) --
    and several declarations may share a line, so neither the line end nor the
    first bracket is where one stops.
    """
    try:
        begin = starts[int(node.get("line")) - 1] + int(node.get("col")) - 1
    except (TypeError, ValueError, IndexError):
        return {}

    colon = source.find(":", begin)
    if colon < 0:
        return {}

    cursor = colon + 1
    mode = _MODE_WORD.match(source, cursor)
    if mode:
        cursor = mode.end()

    type_text, cursor = _until(source, cursor)
    default = ""
    if source[cursor : cursor + 2] == ":=":
        default, _ = _until(source, cursor + 2)

    return {"type": " ".join(type_text.split()), "default": " ".join(default.split())}


def _until(source: str, start: int) -> tuple[str, int]:
    """_until - source up to the end of one declaration item

    Stops at a semicolon or a ":=" outside brackets, or at the bracket that
    closes the clause the declaration sits in.
    """
    depth = 0
    for i in range(start, len(source)):
        char = source[i]
        if char in "([":
            depth += 1
        elif char in ")]":
            if depth == 0:
                return source[start:i], i
            depth -= 1
        elif depth == 0 and (char == ";" or source[i : i + 2] == ":="):
            return source[start:i], i
    return source[start:], len(source)
