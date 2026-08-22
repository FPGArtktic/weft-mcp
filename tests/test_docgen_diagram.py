# SPDX-License-Identifier: GPL-3.0-only
"""Tests for drawing the hierarchy as SVG."""

import re

from weft.docgen.diagram import hierarchy


def node(module, instances=(), resolved=True, **extra):
    return {"module": module, "resolved": resolved, "instances": list(instances), **extra}


def child(name, of, **kwargs):
    return {"name": name, "line": 1, **node(of, **kwargs)}


def boxes(svg):
    return len(re.findall(r"<rect", svg))


def test_one_box_per_node():
    got = hierarchy(node("top", [child("u_a", "leaf"), child("u_b", "leaf")]))
    assert boxes(got) == 3


def test_the_instance_name_labels_the_edge():
    assert ">u_a<" in hierarchy(node("top", [child("u_a", "leaf")]))


def test_a_module_that_is_not_indexed_is_drawn_dashed():
    got = hierarchy(node("top", [child("u_x", "missing", resolved=False)]))
    assert "weft-gap" in got
    assert boxes(got) == 2


def test_the_viewbox_grows_with_the_tree():
    small = hierarchy(node("top", [child("u_a", "leaf")]))
    large = hierarchy(node("top", [child(f"u{i}", "leaf") for i in range(8)]))
    assert _height(large) > _height(small)


def test_markup_in_a_name_cannot_escape_the_drawing():
    got = hierarchy(node("<script>alert(1)</script>"))
    assert "<script>" not in got
    assert "&lt;script&gt;" in got


def test_depth_is_bounded():
    deep = node("l4")
    for level in (3, 2, 1, 0):
        deep = node(f"l{level}", [{"name": f"u{level}", "line": 1, **deep}])
    assert boxes(hierarchy(deep, max_depth=2)) == 3


def test_a_leafless_top_still_draws():
    got = hierarchy(node("solo"))
    assert boxes(got) == 1
    assert ">solo<" in got


def _height(svg):
    return int(re.search(r'height="(\d+)"', svg).group(1))
