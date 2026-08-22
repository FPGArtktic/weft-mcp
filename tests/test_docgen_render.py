# SPDX-License-Identifier: GPL-3.0-only
"""Tests for building a document out of facts."""

from weft.docgen.constraints import Clock, Pin
from weft.docgen.document import MARKDOWN, render
from weft.docgen.render import NOT_COMPILED, PROVENANCE, Project, module_doc, project_doc
from weft.index.model import SYSTEMVERILOG, VHDL, Instance, Module, Parameter, Port
from weft.quartus.reports import ClockTiming, Reports, Usage


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
