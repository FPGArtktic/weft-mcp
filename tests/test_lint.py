# SPDX-License-Identifier: GPL-2.0-only
"""Tests for HDL linting.

The parser tests use output captured verbatim from Verilator 5.050 and
GHDL 6.0.0, so a change in either tool's format fails here rather than in
production.
"""

import pytest

from weft import podman
from weft.fastloop import lint as lintmod
from weft.fastloop.lint import Diagnostic, LintError, lint

VERILATOR_OUTPUT = """\
%Warning-WIDTHEXPAND: src/clk_tick.v:37:26: Operator EQ expects 32 or 26 bits on the LHS, \
but LHS's VARREF 'cnt' generates 24 bits.
                                          : ... note: In instance 'quartus_test.u_tick'
   37 |         end else if (cnt == DIVISOR - 1) begin
      |                          ^~
                      ... For warning description see https://verilator.org/warn/WIDTHEXPAND
%Error: src/bad.sv:5:9: Can't find definition of variable: 'undefined_signal'
    5 |         undefined_signal <= 1'b1;
      |         ^~~~~~~~~~~~~~~~
%Error: Exiting due to 1 error(s)
"""

GHDL_OUTPUT = """\
bad.vhd:9:13:error: ';' expected at end of signal assignment
    z <= '0'
            ^
bad.vhd:9:13:error: (found: 'end')
warn.vhd:7:12:warning: signal "never_used" is never referenced [-Wunused]
"""


@pytest.fixture
def canned(monkeypatch):
    """canned - answer every container run with a fixed transcript."""

    def install(output, returncode=0):
        seen = {}

        def fake_run(image, workspace, argv, timeout=None):
            seen["argv"] = argv
            return podman.Result(returncode, output, "")

        monkeypatch.setattr(lintmod.podman, "run", fake_run)
        return seen

    return install


def test_verilator_output_is_parsed(image, tmp_path, canned):
    canned(VERILATOR_OUTPUT)
    got = lint(image, tmp_path, ["a.sv"], "systemverilog")
    assert got == [
        Diagnostic(
            file="src/clk_tick.v",
            line=37,
            column=26,
            severity="warning",
            message="Operator EQ expects 32 or 26 bits on the LHS, "
            "but LHS's VARREF 'cnt' generates 24 bits.",
            code="WIDTHEXPAND",
        ),
        Diagnostic(
            file="src/bad.sv",
            line=5,
            column=9,
            severity="error",
            message="Can't find definition of variable: 'undefined_signal'",
            code=None,
        ),
    ]


def test_verilator_tally_line_is_dropped(image, tmp_path, canned):
    """ "Exiting due to N error(s)" counts diagnostics; it is not one."""
    canned(VERILATOR_OUTPUT)
    got = lint(image, tmp_path, ["a.sv"], "verilog")
    assert all("Exiting due to" not in d.message for d in got)


def test_ghdl_output_is_parsed(image, tmp_path, canned):
    canned(GHDL_OUTPUT)
    got = lint(image, tmp_path, ["a.vhd"], "vhdl")
    assert [(d.severity, d.line, d.code) for d in got] == [
        ("error", 9, None),
        ("error", 9, None),
        ("warning", 7, "-Wunused"),
    ]
    assert got[2].message == 'signal "never_used" is never referenced'


def test_clean_sources_give_no_diagnostics(image, tmp_path, canned):
    canned("")
    assert lint(image, tmp_path, ["a.sv"], "verilog") == []


def test_verilator_gets_include_paths(image, tmp_path, canned):
    (tmp_path / "src").mkdir()
    (tmp_path / "tb").mkdir()
    seen = canned("")
    lint(image, tmp_path, ["src/a.sv", "tb/b.sv"], "verilog")
    assert seen["argv"][:2] == ["verilator", "--lint-only"]
    assert "-Isrc" in seen["argv"] and "-Itb" in seen["argv"]


def test_ghdl_gets_wall_and_a_scratch_library(image, tmp_path, canned):
    seen = canned("")
    lint(image, tmp_path, ["a.vhd"], "vhdl")
    assert seen["argv"][:4] == ["ghdl", "-a", "--std=08", "-Wall"]
    assert "--workdir=/tmp" in seen["argv"]


def test_paths_are_relative_to_the_mount(image, tmp_path, canned):
    """Relative paths make the tool quote diagnostics back workspace-relative."""
    (tmp_path / "src").mkdir()
    seen = canned("")
    lint(image, tmp_path, [tmp_path / "src" / "a.sv"], "verilog")
    assert seen["argv"][-1] == "src/a.sv"


def test_unknown_language_is_rejected(image, tmp_path):
    with pytest.raises(LintError, match="unknown language"):
        lint(image, tmp_path, ["a.sv"], "chisel")


def test_empty_file_list_is_rejected(image, tmp_path):
    with pytest.raises(LintError, match="no files"):
        lint(image, tmp_path, [], "verilog")


def test_escaping_path_is_rejected(image, tmp_path, canned):
    canned("")
    with pytest.raises(Exception, match="escapes the workspace"):
        lint(image, tmp_path, ["../outside.sv"], "verilog")


@pytest.mark.container
def test_real_verilator_reports_a_real_error(image, tmp_path):
    (tmp_path / "bad.sv").write_text(
        "module bad (input logic clk);\n"
        "    always_ff @(posedge clk) undefined_signal <= 1'b1;\n"
        "endmodule\n"
    )
    got = lint(image, tmp_path, ["bad.sv"], "systemverilog")
    assert any(d.severity == "error" and d.file == "bad.sv" for d in got)


@pytest.mark.container
def test_real_ghdl_reports_a_real_warning(image, tmp_path):
    (tmp_path / "warn.vhd").write_text(
        "library ieee;\n"
        "use ieee.std_logic_1164.all;\n"
        "entity warn is\n"
        "    port (a : in std_logic; y : out std_logic);\n"
        "end entity warn;\n"
        "architecture rtl of warn is\n"
        "    signal never_used : std_logic;\n"
        "begin\n"
        "    y <= a;\n"
        "end architecture rtl;\n"
    )
    got = lint(image, tmp_path, ["warn.vhd"], "vhdl")
    assert [(d.severity, d.code) for d in got] == [("warning", "-Wunused")]


@pytest.mark.container
def test_real_clean_source_is_silent(image, tmp_path):
    (tmp_path / "ok.sv").write_text(
        "module ok (input logic a, output logic y);\n    assign y = a;\nendmodule\n"
    )
    assert lint(image, tmp_path, ["ok.sv"], "systemverilog") == []
