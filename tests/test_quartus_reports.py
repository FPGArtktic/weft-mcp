# SPDX-License-Identifier: GPL-3.0-only
"""Tests for report parsing.

Every fragment here is captured verbatim from a real Quartus 25.1std compile,
so a change in the report format fails here rather than in front of a client.
"""

import pytest

from weft.quartus.reports import (
    ReportError,
    Usage,
    output_directory,
    parse_reports,
)

FIT_SUMMARY = """\
Fitter Status : Successful - Sat Aug 22 17:58:59 2026
Quartus Prime Version : 25.1std.0 Build 1129 10/21/2025 SC Lite Edition
Revision Name : counter
Top-level Entity Name : counter_top
Family : MAX 10
Device : 10M04SAE144A7G
Timing Models : Final
Total logic elements : 177 / 4,032 ( 4 % )
    Total combinational functions : 173 / 4,032 ( 4 % )
    Dedicated logic registers : 108 / 4,032 ( 3 % )
Total registers : 108
Total pins : 20 / 101 ( 20 % )
Total memory bits : 0 / 193,536 ( 0 % )
"""

#: Quartus writes "< 1 %" for anything below a percent, captured verbatim.
SMALL_SUMMARY = """\
Fitter Status : Successful - Sat Aug 22 19:47:30 2026
Revision Name : blink
Total logic elements : 41 / 4,032 ( 1 % )
    Dedicated logic registers : 25 / 4,032 ( < 1 % )
Total registers : 25
"""

STA_SUMMARY = """\
------------------------------------------------------------
Timing Analyzer Summary
------------------------------------------------------------

Type  : Slow 1200mV 125C Model Setup 'clk'
Slack : 13.516
TNS   : 0.000

Type  : Slow 1200mV 125C Model Hold 'clk'
Slack : 0.346
TNS   : 0.000

Type  : Fast 1200mV -40C Model Hold 'clk'
Slack : 0.144
TNS   : -1.250

Type  : Slow 1200mV -40C Model Setup 'clk'
Slack : 14.714
TNS   : 0.000
"""

STA_REPORT = """\
+---------------------------------------------------+
; Slow 1200mV 125C Model Fmax Summary               ;
+------------+-----------------+------------+------+
; Fmax       ; Restricted Fmax ; Clock Name ; Note ;
+------------+-----------------+------------+------+
; 154.23 MHz ; 154.23 MHz      ; clk        ;      ;
+------------+-----------------+------------+------+
This panel reports FMAX for every clock in the design.

+---------------------------------------------------+
; Slow 1200mV -40C Model Fmax Summary               ;
+------------+-----------------+------------+------+
; Fmax       ; Restricted Fmax ; Clock Name ; Note ;
+------------+-----------------+------------+------+
; 189.18 MHz ; 189.18 MHz      ; clk        ;      ;
+------------+-----------------+------------+------+
"""

FIT_REPORT = """\
Critical Warning (169085): No exact pin location assignment(s) for 20 pins
Warning (15714): Some pins have incomplete I/O assignments
Info (176273): Performing register packing
Warning (15714): Some pins have incomplete I/O assignments
"""

POW_REPORT = """\
Warning (215044): No board thermal model was selected.
Error (12345): something went badly wrong
"""


def project(tmp_path, output="output_files", **reports):
    """project - a project directory holding the reports named in @reports."""
    (tmp_path / "counter.qsf").write_text(
        f"set_global_assignment -name PROJECT_OUTPUT_DIRECTORY {output}\n" if output else ""
    )
    out = tmp_path / output if output else tmp_path
    out.mkdir(parents=True, exist_ok=True)
    for suffix, text in reports.items():
        (out / f"counter.{suffix.replace('_', '.')}").write_text(text)
    return tmp_path


def test_output_directory_follows_the_assignment(tmp_path):
    p = project(tmp_path, output="build", fit_summary=FIT_SUMMARY)
    assert output_directory(p, "counter") == p / "build"


def test_output_directory_defaults_beside_the_project(tmp_path):
    """A .qsf that never set one leaves reports in the project root."""
    p = project(tmp_path, output="", fit_summary=FIT_SUMMARY)
    assert output_directory(p, "counter") == p


def test_header_fields(tmp_path):
    got = parse_reports(project(tmp_path, fit_summary=FIT_SUMMARY), "counter")
    assert got.top_entity == "counter_top"
    assert got.family == "MAX 10"
    assert got.device == "10M04SAE144A7G"
    assert got.status.startswith("Successful")


def test_resources_carry_totals_and_percentages(tmp_path):
    got = parse_reports(project(tmp_path, fit_summary=FIT_SUMMARY), "counter")
    assert got.resources["logic_elements"] == Usage(used=177, available=4032, percent=4.0)
    assert got.resources["pins"] == Usage(used=20, available=101, percent=20.0)


def test_a_bare_count_has_no_total(tmp_path):
    got = parse_reports(project(tmp_path, fit_summary=FIT_SUMMARY), "counter")
    assert got.resources["registers_total"] == Usage(used=108)


def test_timing_keeps_the_worst_corner(tmp_path):
    """A design is only as good as its worst corner, and for hold that is the
    fast one, which never appears in the Fmax panels."""
    p = project(tmp_path, sta_summary=STA_SUMMARY, sta_rpt=STA_REPORT)
    clock = parse_reports(p, "counter").timing[0]
    assert clock.clock == "clk"
    assert clock.slack["Hold"] == 0.144
    assert clock.slack["Setup"] == 13.516
    assert clock.tns["Hold"] == -1.250


def test_fmax_is_the_lowest_slow_corner(tmp_path):
    p = project(tmp_path, sta_summary=STA_SUMMARY, sta_rpt=STA_REPORT)
    clock = parse_reports(p, "counter").timing[0]
    assert clock.fmax_mhz == 154.23
    assert clock.restricted_fmax_mhz == 154.23


def test_met_reports_a_failing_clock(tmp_path):
    failing = STA_SUMMARY.replace("Slack : 13.516", "Slack : -1.802")
    p = project(tmp_path, sta_summary=failing, sta_rpt=STA_REPORT)
    assert parse_reports(p, "counter").timing[0].met is False


def test_no_timing_without_a_timing_run(tmp_path):
    """Synthesised but never fitted is not an error; it just has no timing."""
    got = parse_reports(project(tmp_path, fit_summary=FIT_SUMMARY), "counter")
    assert got.timing == []


def test_messages_are_ranked_and_deduplicated(tmp_path):
    p = project(tmp_path, fit_summary=FIT_SUMMARY, fit_rpt=FIT_REPORT, pow_rpt=POW_REPORT)
    got = parse_reports(p, "counter")
    assert [m.severity for m in got.messages] == [
        "Error",
        "Critical Warning",
        "Warning",
        "Warning",
    ]
    assert got.message_count == 4
    assert not any(m.severity == "Info" for m in got.messages)


def test_a_message_names_the_report_it_came_from(tmp_path):
    p = project(tmp_path, fit_summary=FIT_SUMMARY, fit_rpt=FIT_REPORT, pow_rpt=POW_REPORT)
    got = parse_reports(p, "counter")
    assert next(m for m in got.messages if m.code == "215044").source == "counter.pow.rpt"


def test_the_files_read_are_reported(tmp_path):
    p = project(tmp_path, fit_summary=FIT_SUMMARY, sta_summary=STA_SUMMARY)
    got = parse_reports(p, "counter")
    assert "output_files/counter.fit.summary" in got.files
    assert "output_files/counter.sta.summary" in got.files


def test_nothing_compiled_is_an_error(tmp_path):
    with pytest.raises(ReportError, match="no reports"):
        parse_reports(project(tmp_path), "counter")


def test_a_sub_one_percent_line_is_not_dropped(tmp_path):
    """Quartus writes "< 1 %" below a percent. Failing to match it lost the
    whole resource line, silently."""
    got = parse_reports(project(tmp_path, fit_summary=SMALL_SUMMARY), "counter")
    assert got.resources["registers"] == Usage(used=25, available=4032, percent=None)


def test_met_survives_serialisation(tmp_path):
    """The result is handed to a client through asdict(), which drops
    properties, so met has to be a real field."""
    from dataclasses import asdict

    p = project(tmp_path, sta_summary=STA_SUMMARY, sta_rpt=STA_REPORT)
    serialised = asdict(parse_reports(p, "counter"))
    assert serialised["timing"][0]["met"] is True
