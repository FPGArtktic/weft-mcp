# SPDX-License-Identifier: GPL-3.0-only
"""Tests for reading the kernel-doc header a module carries."""

from weft.index.header import attach, parse
from weft.index.model import SYSTEMVERILOG, VHDL, Module, Parameter, Port

VERILOG_SOURCE = """\
// SPDX-License-Identifier: GPL-3.0-only
/**
 * uart_tx - UART transmitter, 8N1
 * @CLK_HZ:   input clock frequency in Hz
 * @clk:      clock, rising edge active
 * @tx_ready: high when idle
 *
 * The start bit is launched on the cycle after @tx_valid && @tx_ready.
 *
 * A second paragraph, to prove the break survives.
 */
`timescale 1ns/1ps

module uart_tx #(parameter int CLK_HZ = 50_000_000) (input logic clk, output logic tx_ready);
endmodule
"""

VHDL_SOURCE = """\
-- SPDX-License-Identifier: GPL-3.0-only
--
-- seven_seg - hexadecimal digit to seven-segment patterns
-- @nibble: value to display, 0 to F
-- @seg_n:  active-low segments
--
-- Outputs are active low, as a common-anode display needs.
--
library ieee;

entity seven_seg is
end entity;
"""


def test_the_summary_is_the_line_after_the_name():
    header = parse(VERILOG_SOURCE, "uart_tx")
    assert header.summary == "UART transmitter, 8N1"


def test_every_field_is_read():
    header = parse(VERILOG_SOURCE, "uart_tx")
    assert header.fields == {
        "CLK_HZ": "input clock frequency in Hz",
        "clk": "clock, rising edge active",
        "tx_ready": "high when idle",
    }


def test_prose_keeps_its_paragraphs_and_loses_its_wrapping():
    header = parse(VERILOG_SOURCE, "uart_tx")
    assert header.description == (
        "The start bit is launched on the cycle after @tx_valid && @tx_ready.\n\n"
        "A second paragraph, to prove the break survives."
    )


def test_a_reference_inside_prose_is_not_a_field():
    """@tx_valid appears in the body but was never documented as a field."""
    header = parse(VERILOG_SOURCE, "uart_tx")
    assert "tx_valid" not in header.fields


def test_the_vhdl_comment_syntax_reads_the_same():
    header = parse(VHDL_SOURCE, "seven_seg")
    assert header.summary == "hexadecimal digit to seven-segment patterns"
    assert header.fields["nibble"] == "value to display, 0 to F"
    assert header.description == "Outputs are active low, as a common-anode display needs."


def test_the_spdx_line_above_the_block_is_not_the_summary():
    assert "SPDX" not in parse(VHDL_SOURCE, "seven_seg").summary


def test_a_module_without_a_header_is_not_invented():
    assert parse("module bare (input clk);\nendmodule\n", "bare") is None


def test_a_header_for_another_module_is_not_borrowed():
    assert parse(VERILOG_SOURCE, "uart_rx") is None


def test_attach_fills_ports_and_parameters():
    module = Module(
        name="uart_tx",
        language=SYSTEMVERILOG,
        file="uart_tx.sv",
        line=14,
        ports=[Port("clk", "in", "logic"), Port("tx_ready", "out", "logic")],
        parameters=[Parameter("CLK_HZ", "50_000_000", "int")],
    )
    got = attach(module, VERILOG_SOURCE)
    assert got.summary == "UART transmitter, 8N1"
    assert [p.doc for p in got.ports] == ["clock, rising edge active", "high when idle"]
    assert got.parameters[0].doc == "input clock frequency in Hz"


def test_attach_matches_names_without_regard_to_case():
    """GHDL folds VHDL identifiers; the header is written by hand."""
    module = Module(
        name="SEVEN_SEG",
        language=VHDL,
        file="seven_seg.vhd",
        line=11,
        ports=[Port("NIBBLE", "in", "std_logic_vector")],
    )
    assert attach(module, VHDL_SOURCE).ports[0].doc == "value to display, 0 to F"


def test_a_port_the_header_forgot_keeps_no_documentation():
    module = Module(
        name="uart_tx",
        language=SYSTEMVERILOG,
        file="uart_tx.sv",
        line=14,
        ports=[Port("clk", "in", "logic"), Port("undocumented", "in", "logic")],
    )
    assert [p.doc for p in attach(module, VERILOG_SOURCE).ports] == [
        "clock, rising edge active",
        None,
    ]


def test_attach_leaves_an_undocumented_module_alone():
    module = Module(name="bare", language=SYSTEMVERILOG, file="bare.sv", line=1)
    assert attach(module, "module bare;\nendmodule\n") == module
