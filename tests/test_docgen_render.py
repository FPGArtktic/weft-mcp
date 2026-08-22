# SPDX-License-Identifier: GPL-3.0-only
"""Tests for building a document out of facts."""

from weft.docgen.constraints import Clock, Pin
from weft.docgen.document import MARKDOWN, render
from weft.docgen.render import (
    NOT_COMPILED,
    PROVENANCE,
    Project,
    compilation_doc,
    module_doc,
    project_doc,
)
from weft.index.model import SYSTEMVERILOG, VHDL, Instance, Module, Parameter, Port
from weft.quartus.reports import ClockTiming, Message, Reports, Usage


def documented():
    return Module(
        name="debouncer",
        language=SYSTEMVERILOG,
        file="src/debouncer.sv",
        line=19,
        ports=[
            Port("clk", "in", "logic", "clock, rising edge active"),
            Port("clean_out", "out", "logic", None),
        ],
        parameters=[Parameter("CLK_HZ", "50_000_000", "int", "clock frequency in Hz")],
        instances=[Instance("u_sync", "synchroniser", 30)],
        summary="input synchroniser and contact debouncer",
        description="Two flip-flops bring the input into the clock domain first.",
    )


def text(blocks):
    return render(blocks, MARKDOWN)


def test_a_module_page_uses_the_authors_own_summary():
    got = text(module_doc(documented()))
    assert "input synchroniser and contact debouncer" in got
    assert "Two flip-flops bring the input" in got


def test_the_port_table_carries_the_header_descriptions():
    got = text(module_doc(documented()))
    assert "| clk | in | logic | clock, rising edge active |" in got


def test_a_port_the_header_forgot_is_left_blank_not_invented():
    """The generator has no idea what clean_out is for, and must not say."""
    got = text(module_doc(documented()))
    assert "| clean_out | out | logic | - |" in got


def test_parameters_and_instances_are_tabulated():
    got = text(module_doc(documented()))
    assert "| CLK_HZ | int | 50_000_000 | clock frequency in Hz |" in got
    assert "| u_sync | synchroniser | 30 |" in got


def test_dependents_are_listed():
    assert "- counter_top" in text(module_doc(documented(), ["counter_top"]))


def test_a_module_without_a_header_still_documents_its_facts():
    bare = Module(
        name="bare",
        language=VHDL,
        file="src/bare.vhd",
        line=3,
        ports=[Port("a", "in", "std_logic")],
    )
    got = text(module_doc(bare))
    assert "# bare" in got
    assert "| a | in | std_logic | - |" in got


def test_a_project_without_a_compilation_says_so():
    got = text(project_doc(Project(name="counter")))
    assert NOT_COMPILED in got


def test_the_pin_map_warns_when_the_fitter_chose_the_pins():
    project = Project(
        name="counter",
        pins=[Pin("clk", "28", "input", "2.5 V", "2", assigned=False)],
    )
    got = text(project_doc(project))
    assert "placed by the fitter rather than constrained" in got
    assert "| clk | 28 | input | 2.5 V | 2 | no |" in got


def test_a_fully_constrained_pin_map_carries_no_warning():
    project = Project(name="counter", pins=[Pin("clk", "PIN_27", assigned=True)])
    assert "placed by the fitter" not in text(project_doc(project))


def test_clocks_are_tabulated_with_their_frequency():
    project = Project(
        name="counter",
        clocks=[Clock("clk", 20.0, 50.0, "clk", False, "counter.sdc")],
    )
    assert "| clk | 20 | 50 | clk | counter.sdc |" in text(project_doc(project))


def test_resources_and_timing_come_from_the_reports():
    reports = Reports(
        status="Successful",
        revision="counter",
        top_entity="counter_top",
        family="MAX 10",
        device="10M04SAE144A7G",
        quartus_version="25.1std.0",
        resources={"logic_elements": Usage(177, 4032, 4.0)},
        timing=[ClockTiming("clk", 154.23, 154.23, {"setup": 0.144}, {"setup": 0.0}, True)],
        messages=[],
        message_count=6,
    )
    got = text(project_doc(Project(name="counter", reports=reports)))
    assert "| logic_elements | 177 | 4032 | 4 |" in got
    assert "| clk | 154.23 | 154.23 | 0.144 | yes |" in got
    assert "6 warnings and errors" in got


def test_the_hierarchy_is_drawn_when_the_top_is_indexed():
    project = Project(
        name="counter",
        top={"module": "counter_top", "resolved": True, "instances": []},
    )
    got = text(project_doc(project))
    assert "```mermaid" in got
    assert "graph TD" in got


def test_no_hierarchy_section_without_a_top():
    assert "## Hierarchy" not in text(project_doc(Project(name="counter")))


def test_a_generated_document_says_that_it_is_generated():
    """Hand edits are lost on the next run; the file has to say so itself."""
    assert PROVENANCE in text(module_doc(documented()))
    assert PROVENANCE in text(project_doc(Project(name="counter")))


def test_a_generated_document_carries_no_timestamp():
    """A file that changes on every run cannot be diffed or committed."""
    assert text(project_doc(Project(name="counter"))) == text(project_doc(Project(name="counter")))


def compiled(messages=(), timing=None, status="Successful"):
    return Reports(
        status=status,
        revision="counter",
        top_entity="counter_top",
        family="MAX 10",
        device="10M04SAE144A7G",
        quartus_version="25.1std.0",
        resources={"logic_elements": Usage(177, 4032, 4.0), "pins": Usage(20, 101, 20.0)},
        timing=timing
        if timing is not None
        else [
            ClockTiming("clk", 154.23, 154.23, {"Setup": 13.5, "Hold": 0.144}, {"Setup": 0.0}, True)
        ],
        messages=list(messages),
        message_count=len(messages),
        files=["output_files/counter.fit.summary"],
    )


CRITICAL = Message("Critical Warning", "169085", "No exact pin location", "counter.fit.rpt")
ROUTINE = Message("Warning", "215044", "No board thermal model was selected", "counter.pow.rpt")
BROKEN = Message("Error", "12345", "something did not fit", "counter.fit.rpt")


def test_the_compilation_doc_leads_with_the_verdict():
    """A developer wants three answers before any table."""
    got = text(compilation_doc("counter", compiled()))
    assert got.index("## Verdict") < got.index("## Resources")
    assert "Status: Successful." in got
    assert "Timing met on 1 of 1 clock." in got


def test_a_critical_warning_is_not_counted_as_an_error():
    """Two critical warnings and no errors is a build that succeeded."""
    got = text(compilation_doc("counter", compiled([CRITICAL, ROUTINE])))
    assert "1 critical warning to look at." in got
    assert "error" not in got.split("## Verdict")[1].split("|")[0]


def test_errors_and_critical_warnings_are_counted_apart():
    got = text(compilation_doc("counter", compiled([BROKEN, CRITICAL])))
    assert "1 error and 1 critical warning to look at." in got
    assert "2 total, 1 error, 1 critical" in got


def test_what_needs_attention_is_kept_out_of_the_routine_warnings():
    """Quartus prints the routine ones every time; burying two in six is how
    they get missed."""
    got = text(compilation_doc("counter", compiled([CRITICAL, ROUTINE])))
    attention = got.split("## Needs attention")[1].split("##")[0]
    assert "No exact pin location" in attention
    assert "thermal model" not in attention
    assert "thermal model" in got.split("## Other warnings")[1]


def test_a_clean_compilation_says_so_rather_than_showing_an_empty_table():
    got = text(compilation_doc("counter", compiled()))
    assert "No errors and no critical warnings." in got


def test_the_worst_slack_names_its_check():
    """A bare 0.144 reads as setup, and hold is usually the smaller number."""
    got = text(compilation_doc("counter", compiled()))
    assert "0.144 ns (Hold, clk)" in got


def test_the_lowest_fmax_names_its_clock():
    reports = compiled(
        timing=[
            ClockTiming("fast", 300.0, 300.0, {"Setup": 2.0}, {"Setup": 0.0}, True),
            ClockTiming("slow", 80.0, 80.0, {"Setup": 1.0}, {"Setup": 0.0}, True),
        ]
    )
    assert "80 MHz (slow)" in text(compilation_doc("counter", reports))


def test_timing_detail_shows_every_check_not_only_the_worst():
    got = text(compilation_doc("counter", compiled()))
    timing = got.split("## Timing")[1]
    assert "Setup: slack / TNS (ns)" in timing
    assert "Hold: slack / TNS (ns)" in timing


def test_a_failed_clock_is_reported_as_not_met():
    reports = compiled(
        timing=[ClockTiming("clk", 40.0, 40.0, {"Setup": -1.2}, {"Setup": -8.0}, False)]
    )
    got = text(compilation_doc("counter", reports))
    assert "Timing met on 0 of 1 clock." in got
    assert "-1.2 ns (Setup, clk)" in got


def test_the_report_files_are_named():
    """Every figure above came out of one of them."""
    assert "`output_files/counter.fit.summary`" in text(compilation_doc("counter", compiled()))
