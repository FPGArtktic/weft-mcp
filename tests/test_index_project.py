# SPDX-License-Identifier: GPL-2.0-only
"""Tests for indexing a directory.

The container tests run the real parsers over a small mixed-language project,
which is the case the whole index exists for: no single parser sees all of it.
"""

import pytest

from weft.index.indexer import IndexError_, index_project
from weft.index.store import SymbolStore
from weft.sandbox import SandboxError

TOP = """\
`timescale 1ns/1ps
module top (input logic clk, output logic [6:0] seg_n);
    logic [3:0] nibble;
    decoder u_dec (.nibble(nibble), .seg_n(seg_n));
    divider u_div (.clk(clk), .tick(nibble[0]));
endmodule
"""

DIVIDER = """\
module divider (input wire clk, output reg tick);
    always @(posedge clk) tick <= ~tick;
endmodule
"""

DECODER = """\
library ieee;
use ieee.std_logic_1164.all;

entity decoder is
    port (nibble : in  std_logic_vector(3 downto 0);
          seg_n  : out std_logic_vector(6 downto 0));
end entity decoder;

architecture rtl of decoder is
begin
    seg_n <= (others => '0');
end architecture rtl;
"""


@pytest.fixture
def design(tmp_path):
    """design - a three-language project inside a workspace."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "top.sv").write_text(TOP)
    (root / "divider.v").write_text(DIVIDER)
    (root / "decoder.vhd").write_text(DECODER)
    return tmp_path


@pytest.fixture
def store(tmp_path):
    s = SymbolStore(tmp_path / "symbols.sqlite")
    yield s
    s.close()


def test_a_missing_directory_is_refused(image, tmp_path, store):
    with pytest.raises(IndexError_, match="not a directory"):
        index_project(image, tmp_path, "nowhere", store)


def test_a_path_outside_the_workspace_is_refused(image, tmp_path, store):
    with pytest.raises(SandboxError, match="escapes the workspace"):
        index_project(image, tmp_path, "../elsewhere", store)


@pytest.mark.container
def test_all_three_languages_are_indexed(image, design, store):
    report = index_project(image, design, "proj", store)
    assert report.failed == {}
    assert report.modules == 3
    assert {m.name for m in store.modules()} == {"top", "divider", "decoder"}


@pytest.mark.container
def test_the_hierarchy_spans_the_languages(image, design, store):
    """No parser here reads more than one language, so the tree is stitched
    from two of them."""
    index_project(image, design, "proj", store)
    tree = store.hierarchy("top")
    got = {i["name"]: (i["module"], i["language"]) for i in tree["instances"]}
    assert got == {"u_dec": ("decoder", "vhdl"), "u_div": ("divider", "verilog")}


@pytest.mark.container
def test_unchanged_files_are_not_parsed_again(image, design, store):
    first = index_project(image, design, "proj", store)
    second = index_project(image, design, "proj", store)
    assert len(first.parsed) == 3
    assert second.parsed == []
    assert len(second.skipped) == 3


@pytest.mark.container
def test_an_edited_file_is_reparsed(image, design, store):
    index_project(image, design, "proj", store)
    (design / "proj" / "divider.v").write_text(
        DIVIDER.replace("output reg tick", "output reg tick, output reg other")
    )
    report = index_project(image, design, "proj", store)
    assert report.parsed == ["proj/divider.v"]
    assert [p.name for p in store.module("divider").ports] == ["clk", "tick", "other"]


@pytest.mark.container
def test_a_removed_file_leaves_the_index(image, design, store):
    index_project(image, design, "proj", store)
    (design / "proj" / "divider.v").unlink()
    report = index_project(image, design, "proj", store)
    assert report.removed == ["proj/divider.v"]
    assert store.module("divider") is None


@pytest.mark.container
def test_a_file_a_parser_rejects_is_reported_not_swallowed(image, design, store):
    (design / "proj" / "broken.vhd").write_text("entity is missing a name;\n")
    report = index_project(image, design, "proj", store)
    assert "proj/broken.vhd" in report.failed
    assert store.module("decoder") is not None
