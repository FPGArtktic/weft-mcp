# SPDX-License-Identifier: GPL-2.0-only
"""Extracting Verilog and SystemVerilog structure from Verible's syntax tree.

Verible parses; this walks what it produced. Node text is recovered from the
byte offsets the leaves carry rather than by joining tokens, so a width or a
default value comes back exactly as the source wrote it, spacing aside.
"""

import json
from pathlib import Path

from .model import IN, INOUT, OUT, SYSTEMVERILOG, VERILOG, Instance, Module, Parameter, Port

#: Direction keywords appear as a direct child of the port declaration, with
#: the keyword itself as the node's tag.
DIRECTIONS = {"input": IN, "output": OUT, "inout": INOUT}

#: A .sv file is SystemVerilog; everything else Verible reads is Verilog.
SUFFIX_LANGUAGE = {".sv": SYSTEMVERILOG, ".svh": SYSTEMVERILOG}


class ParseError(ValueError):
    """Verible's output cannot be read as a syntax tree."""


def modules(payload: str, sources: dict[str, str]) -> list[Module]:
    """modules - every module in one verible-verilog-syntax run

    @payload: the tool's --export_json output, covering one or more files
    @sources: the text of each file, keyed by the path Verible was given

    Return: the modules found, in the order the files were parsed.

    Raises ParseError if the payload is not the expected JSON shape.
    """
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as e:
        raise ParseError(f"verible did not return JSON: {e}") from e

    found = []
    for path, entry in parsed.items():
        tree = (entry or {}).get("tree")
        if tree is None:
            continue
        text = sources.get(path, "")
        language = SUFFIX_LANGUAGE.get(Path(path).suffix.lower(), VERILOG)
        for node in _find(tree, "kModuleDeclaration"):
            found.append(_module(node, path, text, language))
    return found


def _module(node: dict, path: str, text: str, language: str) -> Module:
    """_module - one kModuleDeclaration, unpacked."""
    header = _first(node, "kModuleHeader")
    name = _identifier(header) or "?"

    return Module(
        name=name,
        language=language,
        file=path,
        line=_line(text, _span(node)[0]),
        ports=_ports(header, text),
        parameters=_parameters(header, text),
        instances=_instances(node, text),
    )


def _ports(header: dict | None, text: str) -> list[Port]:
    """_ports - the ANSI-style port list.

    Non-ANSI headers, where the parentheses hold bare names and the directions
    arrive later as module items, yield no ports here rather than wrong ones.
    """
    if header is None:
        return []

    ports = []
    for node in _find(header, "kPortDeclaration"):
        keyword = next((c for c in _kids(node) if c.get("tag") in DIRECTIONS), None)
        named = next((c for c in _kids(node) if c.get("tag") == "kUnqualifiedId"), None)
        if keyword is None or named is None:
            continue

        name = _identifier(named)
        if name is None:
            continue

        # The type is whatever stands between the direction and the name.
        # Reading it off one node would lose half: Verible hangs `wire` off the
        # declaration as its own keyword child while `reg` and `logic` sit
        # inside the data type, and the width is a third node again. Only the
        # direct child can be taken for the name, too -- the identifier inside
        # `[WIDTH-1:0]` comes first in tree order and is not the port.
        ports.append(
            Port(
                name=name,
                direction=DIRECTIONS[keyword["tag"]],
                type=" ".join(text[_span(keyword)[1] : _span(named)[0]].split()),
            )
        )
    return ports


def _parameters(header: dict | None, text: str) -> list[Parameter]:
    """_parameters - the parameter list in the module header."""
    if header is None:
        return []

    found = []
    for node in _find(header, "kParamDeclaration"):
        name = _identifier(node)
        if name is None:
            continue
        assign = _first(node, "kTrailingAssign")
        found.append(
            Parameter(
                name=name,
                default=_text(_first(assign, "kExpression"), text) if assign else None,
                type=_text(_first(node, "kTypeInfo"), text) or None,
            )
        )
    return found


def _instances(node: dict, text: str) -> list[Instance]:
    """_instances - module instantiations, and not signal declarations

    Both wear the same clothes: a data declaration wrapping an instantiation
    base. What tells them apart is that only an instantiation carries gate
    instances underneath, so a `logic [1:0] sync;` never becomes an instance
    of a module called logic.
    """
    found = []
    for declaration in _find(node, "kDataDeclaration"):
        gates = _find(declaration, "kGateInstance")
        if not gates:
            continue
        of = _text(_first(declaration, "kInstantiationType"), text).split()[0]
        for gate in gates:
            name = _identifier(gate)
            if name:
                found.append(Instance(name=name, of=of, line=_line(text, _span(gate)[0])))
    return found


def _kids(node: dict | None) -> list[dict]:
    """_kids - the dict children of a node, skipping the nulls Verible emits."""
    if not isinstance(node, dict):
        return []
    return [c for c in (node.get("children") or []) if isinstance(c, dict)]


def _find(node: dict | None, tag: str) -> list[dict]:
    """_find - every node with @tag anywhere below @node, in source order."""
    found: list[dict] = []

    def walk(current: dict) -> None:
        if current.get("tag") == tag:
            found.append(current)
        for child in _kids(current):
            walk(child)

    if isinstance(node, dict):
        walk(node)
    return found


def _first(node: dict | None, tag: str) -> dict | None:
    """_first - the first node with @tag, or None."""
    found = _find(node, tag)
    return found[0] if found else None


def _span(node: dict | None) -> tuple[int, int]:
    """_span - the byte range of everything under @node."""
    starts: list[int] = []
    ends: list[int] = []

    def walk(current: dict) -> None:
        if "start" in current and "end" in current:
            starts.append(current["start"])
            ends.append(current["end"])
        for child in _kids(current):
            walk(child)

    if isinstance(node, dict):
        walk(node)
    return (min(starts), max(ends)) if starts else (0, 0)


def _text(node: dict | None, source: str) -> str:
    """_text - the source behind a node, whitespace squeezed."""
    if node is None:
        return ""
    start, end = _span(node)
    return " ".join(source[start:end].split())


def _identifier(node: dict | None) -> str | None:
    """_identifier - the first SymbolIdentifier leaf below @node."""
    if not isinstance(node, dict):
        return None
    if node.get("tag") == "SymbolIdentifier":
        return node.get("text")
    for child in _kids(node):
        found = _identifier(child)
        if found:
            return found
    return None


def _line(text: str, offset: int) -> int:
    """_line - the 1-based line holding a byte offset."""
    return text.count("\n", 0, offset) + 1
