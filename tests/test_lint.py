# SPDX-License-Identifier: GPL-3.0-only
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
            return podman.Result(returncode, output)

        monkeypatch.setattr(lintmod.podman, "run", fake_run)
        return seen

    return install


def test_verilator_output_is_parsed(image, tmp_path, canned):
    canned(VERILATOR_OUTPUT)
    got = lint(image, tmp_path, ["a.sv"], "systemverilog").diagnostics
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
    got = lint(image, tmp_path, ["a.sv"], "verilog").diagnostics
    assert all("Exiting due to" not in d.message for d in got)


def test_ghdl_output_is_parsed(image, tmp_path, canned):
    canned(GHDL_OUTPUT)
    got = lint(image, tmp_path, ["a.vhd"], "vhdl").diagnostics
    assert [(d.severity, d.line, d.code) for d in got] == [
        ("error", 9, None),
        ("error", 9, None),
        ("warning", 7, "-Wunused"),
    ]
    assert got[2].message == 'signal "never_used" is never referenced'


def test_clean_sources_give_no_diagnostics(image, tmp_path, canned):
    canned("")
    assert lint(image, tmp_path, ["a.sv"], "verilog").diagnostics == []


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
    got = lint(image, tmp_path, ["bad.sv"], "systemverilog").diagnostics
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
    got = lint(image, tmp_path, ["warn.vhd"], "vhdl").diagnostics
    assert [(d.severity, d.code) for d in got] == [("warning", "-Wunused")]


@pytest.mark.container
def test_real_clean_source_is_silent(image, tmp_path):
    (tmp_path / "ok.sv").write_text(
        "module ok (input logic a, output logic y);\n    assign y = a;\nendmodule\n"
    )
    assert lint(image, tmp_path, ["ok.sv"], "systemverilog").diagnostics == []


QUESTA_OUTPUT = """\
** Note: (vlog-220) '/opt/questa/modelsim.ini' is used as the ini file.
** Error: src/broken.sv(4): (vlog-2730) Undefined variable: 'undeclared_signal'.
** Error: (vlog-13069) src/broken.sv(7): near "endmodule": syntax error.
** Warning: src/a.vhd(12): (vcom-1246) Range is null.
Errors: 2, Warnings: 1
"""


@pytest.fixture
def hosted(monkeypatch):
    """hosted - answer the host Questa check with a fixed transcript."""

    def install(output=QUESTA_OUTPUT, returncode=1):
        seen = {}

        def fake_check(inst, workspace, scratch, verilog, vhdl, timeout=None):
            seen.update(verilog=list(verilog), vhdl=list(vhdl))
            return podman.Result(returncode, output)

        monkeypatch.setattr(lintmod.questa, "check", fake_check)
        return seen

    return install


def probed(tmp_path):
    return lintmod.questa.Install(root=tmp_path, version="Questa 2025.2", env={})


def test_questa_checks_both_languages_in_one_pass(image, tmp_path, hosted):
    seen = hosted()
    got = lint(
        image,
        tmp_path,
        ["src/top.sv", "src/helper.v", "src/decoder.vhd"],
        linter="questa",
        install=probed(tmp_path),
    )
    assert got.excluded == []
    assert seen["verilog"] == ["src/top.sv", "src/helper.v"]
    assert seen["vhdl"] == ["src/decoder.vhd"]


def test_questa_diagnostics_are_parsed_whichever_side_the_id_is_on(image, tmp_path, hosted):
    """Questa puts the message id before or after the location, by pass."""
    hosted()
    got = lint(image, tmp_path, ["src/broken.sv"], linter="questa", install=probed(tmp_path))
    assert [(d.file, d.line, d.severity, d.code) for d in got.diagnostics] == [
        ("src/broken.sv", 4, "error", "vlog-2730"),
        ("src/broken.sv", 7, "error", "vlog-13069"),
        ("src/a.vhd", 12, "warning", "vcom-1246"),
    ]


def test_the_ini_file_note_is_not_a_diagnostic(image, tmp_path, hosted):
    """It is about Questa's own configuration and appears on every run."""
    hosted()
    got = lint(image, tmp_path, ["src/broken.sv"], linter="questa", install=probed(tmp_path))
    assert all("ini file" not in d.message for d in got.diagnostics)


def test_questa_without_a_configured_install_says_so(image, tmp_path, canned):
    canned("")
    with pytest.raises(LintError, match="no Questa configured"):
        lint(image, tmp_path, ["a.sv"], linter="questa")


def test_a_mixed_set_is_not_handed_to_a_tool_that_cannot_read_it(image, tmp_path, canned):
    """Verilator told about a VHDL entity reports a module that is not
    missing, only written in the other language."""
    canned("")
    with pytest.raises(LintError, match="spans both languages"):
        lint(image, tmp_path, ["a.sv", "b.vhd"])


def test_naming_a_language_narrows_a_mixed_set_and_says_what_it_dropped(image, tmp_path, canned):
    canned("")
    got = lint(image, tmp_path, ["a.sv", "b.vhd"], language="verilog")
    assert got.linter == "verilator"
    assert got.excluded == ["b.vhd"]


def test_the_language_is_inferred_when_it_is_not_given(image, tmp_path, canned):
    canned("")
    assert lint(image, tmp_path, ["a.vhd"]).linter == "ghdl"


def test_an_unknown_linter_is_refused(image, tmp_path, canned):
    canned("")
    with pytest.raises(LintError, match="unknown linter"):
        lint(image, tmp_path, ["a.sv"], linter="spyglass")
