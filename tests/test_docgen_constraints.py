# SPDX-License-Identifier: GPL-3.0-only
"""Tests for reading pin assignments and clock constraints."""

from weft.docgen.constraints import clocks, pins

QSF = """\
set_global_assignment -name FAMILY "MAX 10"
set_global_assignment -name SDC_FILE timing.sdc
set_location_assignment PIN_27 -to clk
set_location_assignment PIN_28 -to "led[10]"
set_location_assignment PIN_29 -to led[9]
set_instance_assignment -name IO_STANDARD "3.3-V LVTTL" -to clk
"""

SDC = """\
create_clock -name sys_clk -period 20.000 [get_ports {clk}]
create_generated_clock -name half -source [get_ports {clk}] -divide_by 2 [get_pins {u_div|q}]
# create_clock -name never -period 5.000 [get_ports {ghost}]
create_clock -name uart -period 8.000 [get_ports {uart_clk}]   ;# 125 MHz
"""

PIN_REPORT = """\
Quartus Prime Version 25.1std.0 Build 1129

Pin Name/Usage               : Location  : Dir.   : I/O Standard      : Voltage : I/O Bank  \
: User Assignment
-------------------------------------------------------------------------------------------------------------
VCC_ONE                      : 1         : power  :                   : 3.0V/3.3V :           :
RESERVED_INPUT_WITH_WEAK_PULLUP : 6      : input  :                   :         : 1A        :
~ALTERA_TDO~                 : 20        : output : 2.5 V             :         : 1B        :
clk                          : 28        : input  : 3.3-V LVTTL       :         : 2         : Y
led[10]                      : 54        : output : 2.5 V             :         : 3         : N
led[9]                       : 55        : output : 2.5 V             :         : 3         : N
GND                          : 12        : gnd    :                   :         :           :
"""


def project(tmp_path, qsf=QSF, sdc=SDC):
    (tmp_path / "top.qsf").write_text(qsf)
    (tmp_path / "timing.sdc").write_text(sdc)
    return tmp_path


def test_clocks_carry_period_and_frequency(tmp_path):
    found = clocks(project(tmp_path), "top")
    assert found[0].name == "sys_clk"
    assert found[0].period_ns == 20.0
    assert found[0].frequency_mhz == 50.0
    assert found[0].target == "clk"


def test_a_generated_clock_is_marked_as_one(tmp_path):
    found = {c.name: c for c in clocks(project(tmp_path), "top")}
    assert found["half"].generated is True
    assert found["sys_clk"].generated is False


def test_a_commented_out_clock_is_not_a_clock(tmp_path):
    """Quartus would never see it, so neither should the documentation."""
    assert "never" not in {c.name for c in clocks(project(tmp_path), "top")}


def test_a_trailing_comment_does_not_swallow_the_clock(tmp_path):
    found = {c.name: c for c in clocks(project(tmp_path), "top")}
    assert found["uart"].frequency_mhz == 125.0


def test_an_sdc_the_project_does_not_declare_is_not_read(tmp_path):
    directory = project(tmp_path)
    (directory / "stray.sdc").write_text("create_clock -name stray -period 1.0 [get_ports {x}]\n")
    assert "stray" not in {c.name for c in clocks(directory, "top")}


def test_a_project_without_constraints_has_no_clocks(tmp_path):
    (tmp_path / "top.qsf").write_text('set_global_assignment -name FAMILY "MAX 10"\n')
    assert clocks(tmp_path, "top") == []


def test_requested_pins_come_from_the_qsf(tmp_path):
    found = pins(project(tmp_path), "top")
    assert [(p.signal, p.location) for p in found] == [
        ("clk", "PIN_27"),
        ("led[9]", "PIN_29"),
        ("led[10]", "PIN_28"),
    ]
    assert all(p.assigned for p in found)


def test_the_io_standard_follows_the_signal(tmp_path):
    found = {p.signal: p for p in pins(project(tmp_path), "top")}
    assert found["clk"].io_standard == "3.3-V LVTTL"
    assert found["led[9]"].io_standard is None


def test_the_fitted_report_wins_over_the_request(tmp_path):
    """The .qsf says what was asked for; the report says where it went."""
    directory = project(tmp_path)
    out = directory / "output_files"
    out.mkdir()
    (out / "top.pin").write_text(PIN_REPORT)
    found = {p.signal: p for p in pins(directory, "top", out)}
    assert found["clk"].location == "28"
    assert found["clk"].bank == "2"


def test_device_pins_are_not_part_of_the_design(tmp_path):
    directory = project(tmp_path)
    out = directory / "output_files"
    out.mkdir()
    (out / "top.pin").write_text(PIN_REPORT)
    names = {p.signal for p in pins(directory, "top", out)}
    assert names == {"clk", "led[9]", "led[10]"}


def test_vector_bits_sort_numerically(tmp_path):
    directory = project(tmp_path)
    out = directory / "output_files"
    out.mkdir()
    (out / "top.pin").write_text(PIN_REPORT)
    assert [p.signal for p in pins(directory, "top", out)] == ["clk", "led[9]", "led[10]"]


def test_a_missing_report_falls_back_to_the_request(tmp_path):
    directory = project(tmp_path)
    found = pins(directory, "top", directory / "output_files")
    assert [p.signal for p in found] == ["clk", "led[9]", "led[10]"]
    assert all(p.assigned for p in found)
