# SPDX-License-Identifier: GPL-2.0-only
"""Tests for behavioural simulation."""

import pytest

from weft import podman
from weft.fastloop import simulate as simmod
from weft.fastloop.simulate import SimulationError, simulate


@pytest.fixture
def canned(monkeypatch):
    """canned - answer the container run with a fixed transcript."""

    def install(output="", returncode=0):
        seen = {}

        def fake_run(image, workspace, argv, timeout=None):
            seen["argv"] = argv
            seen["script"] = argv[-1]
            return podman.Result(returncode, output)

        monkeypatch.setattr(simmod.podman, "run", fake_run)
        return seen

    return install


def test_vhdl_sources_pick_ghdl(image, tmp_path, canned):
    seen = canned()
    got = simulate(image, tmp_path, ["a.vhd"], top="a_tb")
    assert got.simulator == "ghdl"
    assert "ghdl -a --std=08" in seen["script"]
    assert "--vcd=a_tb.vcd" in seen["script"]


def test_verilog_sources_pick_verilator(image, tmp_path, canned):
    seen = canned()
    got = simulate(image, tmp_path, ["a.sv"], top="a_tb")
    assert got.simulator == "verilator"
    assert "verilator --binary" in seen["script"]


def test_verilator_warnings_are_not_fatal(image, tmp_path, canned):
    """Reporting warnings is lint's job; simulation must still run."""
    seen = canned()
    simulate(image, tmp_path, ["a.sv"], top="a_tb")
    assert "-Wno-fatal" in seen["script"]


def test_icarus_can_be_asked_for(image, tmp_path, canned):
    seen = canned()
    got = simulate(image, tmp_path, ["a.v"], top="a_tb", simulator="icarus")
    assert got.simulator == "icarus"
    assert "iverilog" in seen["script"]


def test_testbench_is_appended_to_the_sources(image, tmp_path, canned):
    seen = canned()
    simulate(image, tmp_path, ["dut.sv"], top="tb", testbench="tb.sv")
    assert "/work/dut.sv" in seen["script"]
    assert "/work/tb.sv" in seen["script"]


def test_build_products_stay_out_of_the_workspace(image, tmp_path, canned):
    seen = canned()
    simulate(image, tmp_path, ["a.vhd"], top="a_tb")
    assert "--workdir=/tmp/weft-build" in seen["script"]


def test_mixed_language_set_is_refused(image, tmp_path, canned):
    canned()
    with pytest.raises(SimulationError, match="mixed-language"):
        simulate(image, tmp_path, ["a.sv", "b.vhd"], top="tb")


def test_simulator_must_match_the_language(image, tmp_path, canned):
    canned()
    with pytest.raises(SimulationError, match="does not read"):
        simulate(image, tmp_path, ["a.sv"], top="tb", simulator="ghdl")


def test_unknown_simulator_is_refused(image, tmp_path, canned):
    canned()
    with pytest.raises(SimulationError, match="unknown simulator"):
        simulate(image, tmp_path, ["a.sv"], top="tb", simulator="questa")


def test_unrecognised_suffix_is_refused(image, tmp_path, canned):
    canned()
    with pytest.raises(SimulationError, match="unrecognised source type"):
        simulate(image, tmp_path, ["a.chisel"], top="tb")


def test_empty_source_set_is_refused(image, tmp_path, canned):
    canned()
    with pytest.raises(SimulationError, match="no sources"):
        simulate(image, tmp_path, [], top="tb")


def test_failure_is_reported_from_the_exit_status(image, tmp_path, canned):
    canned(output="assertion failed", returncode=1)
    got = simulate(image, tmp_path, ["a.vhd"], top="a_tb")
    assert got.passed is False
    assert got.returncode == 1


def test_log_is_tailed(image, tmp_path, canned):
    canned(output="\n".join(str(i) for i in range(500)))
    got = simulate(image, tmp_path, ["a.vhd"], top="a_tb", log_lines=5)
    assert got.log.splitlines() == ["495", "496", "497", "498", "499"]


def test_no_waveform_when_none_was_written(image, tmp_path, canned):
    canned()
    assert simulate(image, tmp_path, ["a.vhd"], top="a_tb").waveform is None


@pytest.mark.container
def test_real_ghdl_run_passes_and_dumps_a_waveform(image, tmp_path):
    (tmp_path / "inv.vhd").write_text(
        "library ieee;\nuse ieee.std_logic_1164.all;\n"
        "entity inv is port (a : in std_logic; y : out std_logic); end entity;\n"
        "architecture rtl of inv is begin y <= not a; end architecture;\n"
    )
    (tmp_path / "inv_tb.vhd").write_text(
        "library ieee;\nuse ieee.std_logic_1164.all;\n"
        "entity inv_tb is end entity;\n"
        "architecture sim of inv_tb is\n"
        "    signal a, y : std_logic := '0';\n"
        "begin\n"
        "    dut : entity work.inv port map (a => a, y => y);\n"
        "    process begin\n"
        "        a <= '0'; wait for 10 ns;\n"
        "        assert y = '1' report \"inverter is broken\" severity failure;\n"
        "        wait;\n"
        "    end process;\n"
        "end architecture;\n"
    )
    got = simulate(image, tmp_path, ["inv.vhd"], top="inv_tb", testbench="inv_tb.vhd", timeout=300)
    assert got.passed, got.log
    assert got.waveform == ".weft/waves/inv_tb.vcd"
    assert (tmp_path / got.waveform).is_file()


@pytest.mark.container
def test_real_ghdl_run_fails_on_a_failed_assertion(image, tmp_path):
    (tmp_path / "bad_tb.vhd").write_text(
        "entity bad_tb is end entity;\n"
        "architecture sim of bad_tb is begin\n"
        "    process begin\n"
        '        assert false report "deliberate" severity failure;\n'
        "        wait;\n"
        "    end process;\n"
        "end architecture;\n"
    )
    got = simulate(image, tmp_path, ["bad_tb.vhd"], top="bad_tb", timeout=300)
    assert got.passed is False
    assert "deliberate" in got.log


@pytest.mark.container
def test_real_verilator_run_passes(image, tmp_path):
    (tmp_path / "tb.sv").write_text(
        "`timescale 1ns/1ps\n"
        "module tb;\n"
        "    initial begin\n"
        '        $display("hello from verilator");\n'
        "        $finish;\n"
        "    end\n"
        "endmodule\n"
    )
    got = simulate(image, tmp_path, ["tb.sv"], top="tb", timeout=400)
    assert got.passed, got.log
    assert "hello from verilator" in got.log
