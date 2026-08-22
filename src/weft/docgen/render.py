# SPDX-License-Identifier: GPL-3.0-only
"""Turning facts into a document.

Every line here comes from something a tool produced: ports from the AST,
prose from the header the author wrote, pins from the fitter, timing from the
timing analyser. Where a fact is missing the document says so and moves on.
Nothing is inferred, because a reference that guesses is worse than one with a
gap -- the gap is visible and the guess is not.
"""

from dataclasses import dataclass, field
from typing import Any

from ..index.model import Module
from ..quartus.reports import Reports
from . import mermaid
from .constraints import Clock, Pin
from .document import Block, Bullets, Code, Heading, Paragraph, Table

#: Said where a compilation would have supplied the numbers and has not run.
NOT_COMPILED = "No compilation reports were found, so resources and timing are absent."


@dataclass
class Project:
    """What a project document is built from.

    Each field is optional because each comes from a step the user may not
    have taken. A design that has never been fitted still documents its ports
    and its hierarchy; it simply has no pin map.

    @name: revision name
    @info: the .qsf's own summary, as project_info() returns it
    @modules: everything indexed, in name order
    @top: the top entity's hierarchy tree, or None when it is not indexed
    @pins / @clocks: the constraints
    @reports: parsed compilation reports, or None
    """

    name: str
    info: dict[str, Any] = field(default_factory=dict)
    modules: list[Module] = field(default_factory=list)
    top: dict[str, Any] | None = None
    pins: list[Pin] = field(default_factory=list)
    clocks: list[Clock] = field(default_factory=list)
    reports: Reports | None = None


def module_doc(module: Module, instantiated_by: list[str] | None = None) -> list[Block]:
    """module_doc - one module's reference page

    @module: as the index holds it
    @instantiated_by: modules that instantiate this one

    Return: the blocks of the document.
    """
    blocks: list[Block] = [Heading(1, module.name)]

    if module.summary:
        blocks.append(Paragraph(module.summary))
    if module.description:
        blocks.append(Paragraph(module.description))

    blocks.append(Paragraph(f"Defined in `{module.file}:{module.line}` ({module.language})."))

    if module.parameters:
        blocks += [
            Heading(2, "Parameters"),
            Table(
                ["Name", "Type", "Default", "Description"],
                [[p.name, p.type or "", p.default or "", p.doc or ""] for p in module.parameters],
            ),
        ]

    if module.ports:
        blocks += [
            Heading(2, "Ports"),
            Table(
                ["Name", "Direction", "Type", "Description"],
                [[p.name, p.direction, p.type, p.doc or ""] for p in module.ports],
            ),
        ]

    if module.instances:
        blocks += [
            Heading(2, "Instances"),
            Table(
                ["Instance", "Module", "Line"],
                [[i.name, i.of, str(i.line)] for i in module.instances],
            ),
        ]

    if instantiated_by:
        blocks += [Heading(2, "Instantiated by"), Bullets(sorted(instantiated_by))]

    return blocks


def project_doc(project: Project) -> list[Block]:
    """project_doc - the project's reference document

    @project: the facts gathered for it

    Return: the blocks of the document, in reading order: what the project is,
    how it is built, where its signals go, and how it came out.
    """
    blocks: list[Block] = [Heading(1, project.name)]
    blocks += _overview(project)
    blocks += _hierarchy(project)
    blocks += _modules(project)
    blocks += _clocks(project)
    blocks += _pins(project)
    blocks += _results(project)
    return blocks


def _overview(project: Project) -> list[Block]:
    """_overview - device, top entity and source counts."""
    info = project.info
    rows = [
        ["Revision", project.name],
        ["Top entity", info.get("top_entity", "")],
        ["Family", info.get("family", "")],
        ["Device", info.get("device", "")],
    ]
    sources = info.get("sources") or {}
    for language, files in sorted(sources.items()):
        rows.append([f"{language} sources", str(len(files))])

    return [Heading(2, "Overview"), Table(["", ""], rows)]


def _hierarchy(project: Project) -> list[Block]:
    """_hierarchy - the instance tree, drawn."""
    if project.top is None:
        return []
    return [
        Heading(2, "Hierarchy"),
        Code(mermaid.hierarchy(project.top), language="mermaid"),
    ]


def _modules(project: Project) -> list[Block]:
    """_modules - one line per module, with the summary its author wrote."""
    if not project.modules:
        return []
    return [
        Heading(2, "Modules"),
        Table(
            ["Module", "Language", "Summary", "File"],
            [[m.name, m.language, m.summary or "", m.file] for m in project.modules],
        ),
    ]


def _clocks(project: Project) -> list[Block]:
    """_clocks - the constrained clock domains."""
    if not project.clocks:
        return []
    return [
        Heading(2, "Clocks"),
        Table(
            ["Clock", "Period (ns)", "Frequency (MHz)", "Target", "Source"],
            [
                [
                    c.name,
                    _number(c.period_ns),
                    _number(c.frequency_mhz),
                    c.target or "",
                    f"{c.file}{' (generated)' if c.generated else ''}",
                ]
                for c in project.clocks
            ],
        ),
    ]


def _pins(project: Project) -> list[Block]:
    """_pins - the pin map, and where it came from."""
    if not project.pins:
        return []

    chosen = sum(1 for p in project.pins if not p.assigned)
    blocks: list[Block] = [Heading(2, "Pin map")]
    if chosen:
        blocks.append(
            Paragraph(
                f"{chosen} of {len(project.pins)} signals were placed by the fitter rather "
                "than constrained; those locations change whenever the design is refitted."
            )
        )
    blocks.append(
        Table(
            ["Signal", "Pin", "Direction", "I/O standard", "Bank", "Assigned"],
            [
                [
                    p.signal,
                    p.location,
                    p.direction or "",
                    p.io_standard or "",
                    p.bank or "",
                    "yes" if p.assigned else "no",
                ]
                for p in project.pins
            ],
        )
    )
    return blocks


def _results(project: Project) -> list[Block]:
    """_results - resources and timing, when a compilation produced them."""
    reports = project.reports
    if reports is None:
        return [Heading(2, "Compilation results"), Paragraph(NOT_COMPILED)]

    blocks: list[Block] = [Heading(2, "Compilation results")]
    if reports.status:
        blocks.append(Paragraph(f"Status: {reports.status}"))

    if reports.resources:
        blocks += [
            Heading(3, "Resources"),
            Table(
                ["Resource", "Used", "Available", "%"],
                [
                    [
                        name,
                        str(usage.used),
                        "" if usage.available is None else str(usage.available),
                        _number(usage.percent),
                    ]
                    for name, usage in reports.resources.items()
                ],
            ),
        ]

    if reports.timing:
        blocks += [
            Heading(3, "Timing"),
            Table(
                ["Clock", "Fmax (MHz)", "Restricted Fmax (MHz)", "Worst slack (ns)", "Met"],
                [
                    [
                        t.clock,
                        _number(t.fmax_mhz),
                        _number(t.restricted_fmax_mhz),
                        _number(min(t.slack.values()) if t.slack else None),
                        "yes" if t.met else "no",
                    ]
                    for t in reports.timing
                ],
            ),
        ]

    if reports.message_count:
        blocks.append(
            Paragraph(
                f"{reports.message_count} warnings and errors were reported; "
                "parse_reports returns them ranked."
            )
        )
    return blocks


def _number(value: float | None) -> str:
    """_number - a figure without a trailing .0, or nothing at all."""
    if value is None:
        return ""
    if float(value).is_integer():
        return str(int(value))
    return f"{value:g}"
