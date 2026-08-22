# SPDX-License-Identifier: GPL-3.0-only
"""The instance hierarchy drawn as SVG, for the HTML document.

Mermaid is the right answer where something renders it -- GitHub does, and so
do the Markdown viewers worth using. A browser opening a file does not, and an
offline server may not fetch a rendering library to make it. An HTML page that
shows the source of a diagram instead of the diagram is not documentation.

So the same tree is drawn twice, once as Mermaid text for Markdown and once as
SVG for HTML. SVG needs nothing: no script, no font, no network.

The layout is one column per level, one row per leaf, and a parent centred on
its children. That is enough for a design hierarchy, which is a tree that is
wide at the bottom and shallow -- the algorithms that do better exist to pack
graphs this one never becomes.
"""

from dataclasses import dataclass, field
from html import escape
from typing import Any

#: Geometry, in user units, which are CSS pixels here.
BOX_HEIGHT = 28
ROW_HEIGHT = 46
COLUMN_GAP = 64
PADDING = 12
CHAR_WIDTH = 7.4
FONT_SIZE = 13
LABEL_SIZE = 11

#: How deep to draw, matching the Mermaid renderer.
MAX_DEPTH = 12


@dataclass
class _Node:
    """One box, once the tree has been laid out."""

    label: str
    edge: str | None
    depth: int
    resolved: bool
    row: float = 0.0
    children: list["_Node"] = field(default_factory=list)


def hierarchy(tree: dict[str, Any], max_depth: int = MAX_DEPTH) -> str:
    """hierarchy - an instance tree as a standalone SVG element

    @tree: a node as SymbolStore.hierarchy() returns it
    @max_depth: levels to draw below the top

    Return: the <svg> element, ready to embed in a page. Nothing outside it is
    needed to display it.
    """
    root = _build(tree, None, 0, max_depth)
    leaves = _rows(root, 0)

    columns = _widths(root)
    offsets = _offsets(columns)
    width = offsets[-1] + PADDING
    height = int(max(leaves, 1) * ROW_HEIGHT + PADDING)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {int(width)} {height}"'
        f' width="{int(width)}" height="{height}" role="img"'
        ' aria-label="instance hierarchy" style="max-width:100%;height:auto">',
        "<style>"
        f".weft-box{{fill:#f6f6f6;stroke:#444}}"
        f".weft-gap{{fill:none;stroke:#444;stroke-dasharray:4 3}}"
        f".weft-name{{font:{FONT_SIZE}px ui-monospace,monospace;fill:#111}}"
        f".weft-edge{{font:{LABEL_SIZE}px ui-monospace,monospace;fill:#555}}"
        f".weft-line{{fill:none;stroke:#888}}"
        "</style>",
    ]
    _draw(root, columns, offsets, parts)
    parts.append("</svg>")
    return "".join(parts)


def _build(node: dict[str, Any], edge: str | None, depth: int, limit: int) -> _Node:
    """_build - the drawable tree, cut at @limit levels."""
    label = str(node.get("module", "?"))
    if node.get("recursive"):
        label += " ↺"

    built = _Node(
        label=label,
        edge=edge,
        depth=depth,
        resolved=bool(node.get("resolved", True)),
    )
    if depth < limit:
        built.children = [
            _build(child, str(child.get("name", "?")), depth + 1, limit)
            for child in node.get("instances", [])
        ]
    return built


def _rows(node: _Node, next_row: int) -> int:
    """_rows - place every node on a row; leaves take one each.

    Return: the next free row, which after the root is the number of leaves.
    """
    if not node.children:
        node.row = next_row + 0.5
        return next_row + 1

    for child in node.children:
        next_row = _rows(child, next_row)
    node.row = (node.children[0].row + node.children[-1].row) / 2
    return next_row


def _widths(node: _Node, found: dict[int, float] | None = None) -> list[float]:
    """_widths - the widest box in each column, so columns do not overlap."""
    found = {} if found is None else found
    box = len(node.label) * CHAR_WIDTH + 16
    found[node.depth] = max(found.get(node.depth, 0.0), box)
    for child in node.children:
        _widths(child, found)
    return [found[d] for d in sorted(found)]


def _offsets(columns: list[float]) -> list[float]:
    """_offsets - the left edge of each column, and the right edge of the last."""
    edges = [float(PADDING)]
    for width in columns:
        edges.append(edges[-1] + width + COLUMN_GAP)
    edges[-1] -= COLUMN_GAP
    return edges


def _draw(node: _Node, columns: list[float], offsets: list[float], parts: list[str]) -> None:
    """_draw - one box, its edges down to its children, and then theirs."""
    x = offsets[node.depth]
    width = columns[node.depth]
    y = node.row * ROW_HEIGHT - BOX_HEIGHT / 2 + PADDING / 2

    css = "weft-box" if node.resolved else "weft-box weft-gap"
    parts.append(
        f'<rect class="{css}" x="{x:.1f}" y="{y:.1f}" width="{width:.1f}"'
        f' height="{BOX_HEIGHT}" rx="4"/>'
    )
    parts.append(
        f'<text class="weft-name" x="{x + width / 2:.1f}" y="{y + BOX_HEIGHT / 2 + 4:.1f}"'
        f' text-anchor="middle">{escape(node.label)}</text>'
    )

    for child in node.children:
        start_x = x + width
        start_y = y + BOX_HEIGHT / 2
        end_x = offsets[child.depth]
        end_y = child.row * ROW_HEIGHT + PADDING / 2
        mid = start_x + COLUMN_GAP / 2

        parts.append(
            f'<path class="weft-line" d="M{start_x:.1f},{start_y:.1f} H{mid:.1f}'
            f' V{end_y:.1f} H{end_x:.1f}"/>'
        )
        if child.edge:
            parts.append(
                f'<text class="weft-edge" x="{mid + 3:.1f}" y="{end_y - 4:.1f}">'
                f"{escape(child.edge)}</text>"
            )
        _draw(child, columns, offsets, parts)
