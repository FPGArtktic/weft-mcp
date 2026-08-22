# SPDX-License-Identifier: GPL-3.0-only
"""The instance hierarchy as a diagram.

Mermaid because it is text: it survives review, diffs like code, and renders
without a drawing tool. GitHub, the MCP clients worth using and most Markdown
viewers draw it; where nothing does, the reader still sees the structure.

One node per instance path rather than per module. A module instantiated three
times is three boxes, which is what the hierarchy actually is; collapsing them
would draw a dependency graph and call it a hierarchy.
"""

import re
from typing import Any

#: Node ids are generated, never taken from the design. A module called `end`
#: or `graph` would otherwise close the diagram it appears in.
_UNSAFE = re.compile(r"[^0-9A-Za-z_]")

#: How deep to draw. A diagram past this is unreadable anyway, and the tree
#: itself is already bounded by the store.
MAX_DEPTH = 12


def hierarchy(tree: dict[str, Any], max_depth: int = MAX_DEPTH) -> str:
    """hierarchy - a Mermaid diagram of an instance tree

    @tree: a node as SymbolStore.hierarchy() returns it
    @max_depth: levels to draw below the top

    Modules that are instantiated but not indexed are drawn with a dashed
    border rather than left out. A gap in a hierarchy diagram reads as "this
    design has no such block", which is the wrong thing to conclude from "the
    indexer never saw the file".

    Return: the diagram, without its fence, ready to embed.
    """
    lines = ["graph TD"]
    unresolved: list[str] = []
    counter = _Counter()

    root = counter.next(tree.get("module", "?"))
    lines.append(f'    {root}["{_label(tree.get("module", "?"))}"]')
    if not tree.get("resolved", True):
        unresolved.append(root)

    _walk(tree, root, lines, unresolved, counter, max_depth)

    for node in unresolved:
        lines.append(f"    style {node} stroke-dasharray: 4 3")
    return "\n".join(lines)


def _walk(
    node: dict[str, Any],
    parent: str,
    lines: list[str],
    unresolved: list[str],
    counter: "_Counter",
    depth: int,
) -> None:
    """_walk - draw one node's children, and theirs."""
    if depth <= 0:
        return

    for child in node.get("instances", []):
        name = counter.next(child.get("of", "?"))
        label = _label(child.get("of", "?"))
        if child.get("recursive"):
            label += " ↺"
        lines.append(f'    {name}["{label}"]')
        lines.append(f"    {parent} -->|{_edge(child.get('name', '?'))}| {name}")
        if not child.get("resolved", True):
            unresolved.append(name)
        _walk(child, name, lines, unresolved, counter, depth - 1)


def _label(text: str) -> str:
    """_label - a node caption Mermaid will not read as syntax."""
    return text.replace('"', "'").replace("[", "(").replace("]", ")")


def _edge(text: str) -> str:
    """_edge - an edge caption; a pipe would end it early."""
    return _label(text).replace("|", "/")


class _Counter:
    """Unique, syntax-safe node ids.

    The module name is kept in the id so a raw diagram is still readable, and
    a counter is appended so two instances of one module do not collide.
    """

    def __init__(self) -> None:
        self._used = 0

    def next(self, name: str) -> str:
        self._used += 1
        return f"n{self._used}_{_UNSAFE.sub('_', name)}"
