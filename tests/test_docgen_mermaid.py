# SPDX-License-Identifier: GPL-3.0-only
"""Tests for drawing the instance hierarchy."""

from weft.docgen.mermaid import hierarchy


def node(module, instances=(), resolved=True, **extra):
    return {
        "module": module,
        "resolved": resolved,
        "instances": list(instances),
        **extra,
    }


def child(name, of, **kwargs):
    return {"name": name, "of": of, "line": 1, **node(of, **kwargs)}


def test_the_top_is_drawn():
    got = hierarchy(node("top"))
    assert got.startswith("graph TD")
    assert '["top"]' in got


def test_each_instance_is_its_own_box():
    """Two instances of one module are two blocks, not one shared node."""
    got = hierarchy(node("top", [child("u_a", "leaf"), child("u_b", "leaf")]))
    assert got.count('["leaf"]') == 2


def test_edges_are_labelled_with_the_instance_name():
    got = hierarchy(node("top", [child("u_a", "leaf")]))
    assert "|u_a|" in got


def test_an_unindexed_module_is_drawn_dashed_rather_than_dropped():
    got = hierarchy(node("top", [child("u_x", "missing", resolved=False)]))
    assert '["missing"]' in got
    assert "stroke-dasharray" in got


def test_a_name_that_is_mermaid_syntax_cannot_break_the_diagram():
    got = hierarchy(node("end", [child("graph", "subgraph")]))
    for line in got.splitlines()[1:]:
        assert not line.strip().startswith(("end", "graph", "subgraph"))


def test_brackets_in_a_name_do_not_close_the_node():
    got = hierarchy(node("top", [child("u_a[0]", "leaf")]))
    assert "u_a(0)" in got


def test_depth_is_bounded():
    deep = node("l3")
    for level in (2, 1, 0):
        deep = node(f"l{level}", [{"name": f"u{level}", "line": 1, **deep}])
    assert hierarchy(deep, max_depth=1).count("-->") == 1


def test_a_recursive_node_is_marked():
    got = hierarchy(node("top", [child("u_self", "top", recursive=True)]))
    assert "↺" in got
