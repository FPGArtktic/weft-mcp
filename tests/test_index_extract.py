# SPDX-License-Identifier: GPL-3.0-only
"""Tests for pulling structure out of Verible's and GHDL's output.

These run the real parsers. A fixture would have to be the whole syntax tree --
Verible emits half a megabyte for four small files, GHDL a megabyte and a half
because it dumps the standard libraries too -- and a captured tree would test
this code against a snapshot rather than against the tools it has to survive.
"""

import json

import pytest

from weft import podman
from weft.index.model import IN, OUT, SYSTEMVERILOG, VERILOG, VHDL
from weft.index.verilog import ParseError
from weft.index.verilog import modules as verilog_modules
from weft.index.vhdl import modules as vhdl_modules

SV = """\
`timescale 1ns/1ps
module counter #(
    parameter int WIDTH = 8
) (
    input  logic             clk,
    input  logic             rst_n,
    output logic [WIDTH-1:0] count
);
    always_ff @(posedge clk) count <= count + 1'b1;
endmodule
"""

V2001 = """\
module divider #(parameter DIV = 4) (
    input  wire clk,
    input  wire rst_n,
    output reg  tick
);
    reg [7:0] c;
endmodule
"""

TOP = """\
module top (input logic clk, output logic q);
    logic a, b;
    logic [3:0] wide;
    counter #(.WIDTH(4)) u_one (.clk(clk), .rst_n(1'b1), .count(wide));
    divider u_two (.clk(clk), .rst_n(1'b1), .tick(q));
endmodule
"""

VHD = """\
library ieee;
use ieee.std_logic_1164.all;

entity decoder is
    generic (WIDTH : natural := 4; TAG : string := "hex");
    port (
        nibble : in  std_logic_vector(3 downto 0);
        enable : in  std_logic;
        seg_n  : out std_logic_vector(6 downto 0)
    );
end entity decoder;

architecture rtl of decoder is
begin
    seg_n <= (others => '0');
end architecture rtl;
"""


def verible(image, tmp_path, files: dict[str, str]):
    """verible - parse @files in the container and extract their modules."""
    for name, text in files.items():
        (tmp_path / name).write_text(text)
    result = podman.run(
        image, tmp_path, ["verible-verilog-syntax", "--export_json", "--printtree", *files]
    )
    payload = result.output[result.output.index("{") :]
    return verilog_modules(payload, dict(files))


def ghdl(image, tmp_path, name: str, text: str):
    """ghdl - analyse then dump; the dump analyses, so the library comes first."""
    (tmp_path / name).write_text(text)
    script = f"mkdir -p /tmp/lib && ghdl -a --std=08 --workdir=/tmp/lib {name} && "
    script += f"ghdl --file-to-xml --std=08 --workdir=/tmp/lib {name}"
    result = podman.run(image, tmp_path, ["sh", "-c", script])
    payload = result.output[result.output.index("<?xml") :]
    return vhdl_modules(payload, name, text)


@pytest.mark.container
def test_systemverilog_ports_and_parameters(image, tmp_path):
    got = verible(image, tmp_path, {"counter.sv": SV})[0]
    assert got.name == "counter"
    assert got.language == SYSTEMVERILOG
    assert [(p.name, p.direction, p.type) for p in got.ports] == [
        ("clk", IN, "logic"),
        ("rst_n", IN, "logic"),
        ("count", OUT, "logic [WIDTH-1:0]"),
    ]
    assert [(p.name, p.type, p.default) for p in got.parameters] == [("WIDTH", "int", "8")]


@pytest.mark.container
def test_a_width_expression_is_not_mistaken_for_the_port_name(image, tmp_path):
    """The identifier inside [WIDTH-1:0] comes first in tree order."""
    got = verible(image, tmp_path, {"counter.sv": SV})[0]
    assert got.ports[-1].name == "count"


@pytest.mark.container
def test_verilog_2001_net_types(image, tmp_path):
    """Verible hangs `wire` off the declaration but puts `reg` in the data
    type, so reading either node alone loses half the ports' types."""
    got = verible(image, tmp_path, {"divider.v": V2001})[0]
    assert got.language == VERILOG
    assert [(p.name, p.type) for p in got.ports] == [
        ("clk", "wire"),
        ("rst_n", "wire"),
        ("tick", "reg"),
    ]


@pytest.mark.container
def test_signal_declarations_are_not_instances(image, tmp_path):
    """`logic a, b;` wears the same nodes as an instantiation; only a real
    instantiation carries gate instances underneath."""
    got = [
        m
        for m in verible(image, tmp_path, {"top.sv": TOP, "counter.sv": SV, "divider.v": V2001})
        if m.name == "top"
    ][0]
    assert [(i.name, i.of) for i in got.instances] == [("u_one", "counter"), ("u_two", "divider")]


@pytest.mark.container
def test_vhdl_entity_ports_and_generics(image, tmp_path):
    got = ghdl(image, tmp_path, "decoder.vhd", VHD)[0]
    assert got.name == "decoder"
    assert got.language == VHDL
    assert [(p.name, p.direction, p.type) for p in got.ports] == [
        ("nibble", IN, "std_logic_vector(3 downto 0)"),
        ("enable", IN, "std_logic"),
        ("seg_n", OUT, "std_logic_vector(6 downto 0)"),
    ]


@pytest.mark.container
def test_two_generics_on_one_line_stay_apart(image, tmp_path):
    """Reading the declaration's line would give both of them the same text."""
    got = ghdl(image, tmp_path, "decoder.vhd", VHD)[0]
    assert [(p.name, p.type, p.default) for p in got.parameters] == [
        ("width", "natural", "4"),
        ("tag", "string", '"hex"'),
    ]


def test_bad_payload_is_reported():
    with pytest.raises(ParseError, match="did not return JSON"):
        verilog_modules("not json at all", {})


def test_a_file_verible_could_not_parse_is_skipped():
    """An entry with no tree is a file that failed; the rest still index."""
    assert verilog_modules(json.dumps({"broken.sv": {}}), {"broken.sv": ""}) == []
