# SPDX-License-Identifier: GPL-3.0-only
"""Turning Quartus reports into something small enough to send.

A compile leaves megabytes of .rpt behind. What a caller wants from it is a
resource line, the timing per clock, and the handful of messages that are not
Info. Everything here reads files; no Quartus is run, so reports can be parsed
long after the installation is gone.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

#: Severities worth returning, worst first. Info is left on disk.
SEVERITY_RANK = {"Error": 0, "Critical Warning": 1, "Warning": 2}

#: How many messages a result carries before it stops being small.
MAX_MESSAGES = 40

#: Timing checks the summary reports, in the order an engineer reads them.
CHECKS = ("Setup", "Hold", "Recovery", "Removal", "Minimum Pulse Width")

#: Resource lines worth lifting out of the fitter summary.
RESOURCE_KEYS = {
    "Total logic elements": "logic_elements",
    "Total combinational functions": "combinational_functions",
    "Dedicated logic registers": "registers",
    "Total registers": "registers_total",
    "Total pins": "pins",
    "Total virtual pins": "virtual_pins",
    "Total memory bits": "memory_bits",
    "Embedded Multiplier 9-bit elements": "multipliers",
    "Total PLLs": "plls",
    "Total ALMs": "alms",
    "Total block memory bits": "memory_bits",
    "Total DSP Blocks": "dsp_blocks",
}

_SUMMARY_LINE = re.compile(r"^\s*(?P<key>[^:]+?)\s*:\s*(?P<value>.+?)\s*$")

#: "177 / 4,032 ( 4 % )", "25 / 4,032 ( < 1 % )", or a bare "108".
_USAGE = re.compile(
    r"^(?P<used>[\d,]+)"
    r"(?:\s*/\s*(?P<available>[\d,]+)\s*\(\s*(?P<percent>[<>]?\s*[\d.]+)\s*%\s*\))?$"
)

#: "Type  : Slow 1200mV 125C Model Setup 'clk'"
_TYPE = re.compile(
    r"^Type\s*:\s*(?P<corner>.+?)\s+(?P<check>" + "|".join(CHECKS) + r")\s+'(?P<clock>[^']+)'\s*$"
)
_SLACK = re.compile(r"^Slack\s*:\s*(?P<value>-?[\d.]+)\s*$")
_TNS = re.compile(r"^TNS\s*:\s*(?P<value>-?[\d.]+)\s*$")

#: "; 154.23 MHz ; 154.23 MHz      ; clk        ;      ;"
_FMAX_HEADER = re.compile(r"^;\s*(?P<corner>.+?Model)\s+Fmax Summary\s*;\s*$")
_FMAX_ROW = re.compile(
    r"^;\s*(?P<fmax>[\d.]+)\s*MHz\s*;\s*(?P<restricted>[\d.]+)\s*MHz\s*;\s*(?P<clock>\S+)\s*;"
)

#: "Warning (215044): No board thermal model was selected."
_MESSAGE = re.compile(
    r"^(?P<severity>Critical Warning|Warning|Error)\s*\((?P<code>\d+)\):\s*(?P<text>.+?)\s*$"
)

_OUTPUT_DIR = re.compile(
    r"^\s*set_global_assignment\s+-name\s+PROJECT_OUTPUT_DIRECTORY\s+(?P<value>\S+|\"[^\"]+\")",
    re.M,
)


class ReportError(ValueError):
    """The reports cannot be found or read."""


@dataclass(frozen=True)
class Usage:
    """One resource line.

    @used: how many are in use
    @available: how many the device has, or None when the report gives no total
    @percent: the report's own percentage, or None when it did not give an
              exact one. Quartus writes "< 1 %" below a percent, and rounding
              that to 1.0 would overstate it; @used and @available are exact,
              so nothing is lost by leaving it out.
    """

    used: int
    available: int | None = None
    percent: float | None = None


@dataclass(frozen=True)
class ClockTiming:
    """Worst-case timing for one clock domain, across every corner analysed.

    @clock: clock name as the constraint file spells it
    @fmax_mhz: lowest Fmax over the slow corners, or None when none was
               computed; the fast corners have no Fmax panel at all
    @restricted_fmax_mhz: the same, after device restrictions
    @slack: worst slack per check, keyed by check name
    @tns: total negative slack per check
    @met: whether every check has non-negative slack. A stored field rather
          than a property, because the result is serialised with asdict() and
          a property would never reach the caller.
    """

    clock: str
    fmax_mhz: float | None
    restricted_fmax_mhz: float | None
    slack: dict[str, float]
    tns: dict[str, float]
    met: bool


@dataclass(frozen=True)
class Message:
    """One Warning, Critical Warning or Error.

    @severity: as Quartus spells it
    @code: the numeric message id, stable across versions
    @text: the message
    @source: report file it came from, relative to the output directory
    """

    severity: str
    code: str
    text: str
    source: str


@dataclass(frozen=True)
class Reports:
    """Everything worth returning about one compilation.

    @status: the flow's own status line, e.g. "Successful - <date>"
    @revision / @top_entity / @family / @device: as the fitter reports them
    @quartus_version: the version banner from the report
    @resources: resource lines, keyed by the names in RESOURCE_KEYS
    @timing: one entry per clock domain
    @messages: warnings and errors, worst first, capped
    @message_count: how many there were before the cap
    @files: report files that were read, relative to the project directory
    """

    status: str | None
    revision: str
    top_entity: str | None
    family: str | None
    device: str | None
    quartus_version: str | None
    resources: dict[str, Usage]
    timing: list[ClockTiming]
    messages: list[Message]
    message_count: int
    files: list[str] = field(default_factory=list)


def output_directory(project_dir: Path, revision: str) -> Path:
    """output_directory - where this project's reports actually land

    @project_dir: the project directory
    @revision: revision name, which is the .qsf basename

    Quartus writes reports into PROJECT_OUTPUT_DIRECTORY when the .qsf sets
    one, and beside the project when it does not. Assuming output_files/ would
    be wrong for any project that never set it.

    Return: the directory, whether or not it exists yet.
    """
    qsf = Path(project_dir) / f"{revision}.qsf"
    try:
        text = qsf.read_text(errors="replace")
    except OSError:
        return Path(project_dir)

    m = _OUTPUT_DIR.search(text)
    if m is None:
        return Path(project_dir)
    return Path(project_dir) / m["value"].strip('"')


def parse_reports(project_dir: Path, revision: str) -> Reports:
    """parse_reports - read a finished compilation's reports

    @project_dir: the project directory
    @revision: revision name

    Return: a Reports, with whatever the compilation actually produced. A
    partial compile is not an error: a project synthesised but never fitted
    has resources and no timing, and says so by leaving the lists empty.

    Raises ReportError when no report of any kind exists, which means nothing
    has been compiled.
    """
    project_dir = Path(project_dir)
    out = output_directory(project_dir, revision)

    fit = _read(out / f"{revision}.fit.summary")
    syn = _read(out / f"{revision}.map.summary")
    sta_summary = _read(out / f"{revision}.sta.summary")
    sta_report = _read(out / f"{revision}.sta.rpt")

    read = [
        p
        for p, text in (
            (f"{revision}.fit.summary", fit),
            (f"{revision}.map.summary", syn),
            (f"{revision}.sta.summary", sta_summary),
            (f"{revision}.sta.rpt", sta_report),
        )
        if text is not None
    ]
    if not read:
        raise ReportError(f"no reports for revision {revision} in {out}")

    header = _headers(fit or syn or "")
    messages, total = _messages(out, revision)

    return Reports(
        status=header.get("Fitter Status") or header.get("Analysis & Synthesis Status"),
        revision=revision,
        top_entity=header.get("Top-level Entity Name"),
        family=header.get("Family"),
        device=header.get("Device"),
        quartus_version=header.get("Quartus Prime Version"),
        resources=_resources(fit or syn or ""),
        timing=_timing(sta_summary or "", sta_report or ""),
        messages=messages,
        message_count=total,
        files=[str((out / p).relative_to(project_dir)) for p in read],
    )


def _read(path: Path) -> str | None:
    """_read - a report's text, or None when it was never written."""
    try:
        return path.read_text(errors="replace")
    except OSError:
        return None


def _headers(text: str) -> dict[str, str]:
    """_headers - the "key : value" lines a summary opens with."""
    found = {}
    for line in text.splitlines():
        m = _SUMMARY_LINE.match(line)
        if m and m["key"] not in found:
            found[m["key"]] = m["value"]
    return found


def _resources(text: str) -> dict[str, Usage]:
    """_resources - the resource lines, parsed into counts and totals.

    Device families name their resources differently -- logic elements on a
    MAX 10, ALMs on a Cyclone V -- so whatever the report happens to carry is
    what comes back, rather than a fixed set with holes in it.
    """
    found = {}
    for line in text.splitlines():
        m = _SUMMARY_LINE.match(line)
        if m is None:
            continue
        key = RESOURCE_KEYS.get(m["key"])
        if key is None or key in found:
            continue
        usage = _usage(m["value"])
        if usage is not None:
            found[key] = usage
    return found


def _usage(value: str) -> Usage | None:
    """_usage - "177 / 4,032 ( 4 % )", or a bare count."""
    m = _USAGE.match(value.strip())
    if m is None:
        return None
    percent = m["percent"]
    exact = percent is not None and not percent.lstrip().startswith(("<", ">"))
    return Usage(
        used=int(m["used"].replace(",", "")),
        available=int(m["available"].replace(",", "")) if m["available"] else None,
        percent=float(percent) if exact else None,
    )


def _timing(summary: str, report: str) -> list[ClockTiming]:
    """_timing - worst case per clock, folded across every corner analysed

    The summary carries one block per corner, per check, per clock; a design
    is only as good as its worst corner, so the worst is what survives. Fmax
    comes from the report instead, and only the slow corners have it.
    """
    slack: dict[str, dict[str, float]] = {}
    tns: dict[str, dict[str, float]] = {}

    lines = summary.splitlines()
    for i, line in enumerate(lines):
        m = _TYPE.match(line)
        if m is None:
            continue
        clock, check = m["clock"], m["check"]
        for follower in lines[i + 1 : i + 4]:
            s = _SLACK.match(follower)
            if s:
                _keep_worst(slack, clock, check, float(s["value"]))
            t = _TNS.match(follower)
            if t:
                _keep_worst(tns, clock, check, float(t["value"]))

    fmax, restricted = _fmax(report)

    clocks = sorted(set(slack) | set(fmax))
    return [
        ClockTiming(
            clock=clock,
            fmax_mhz=fmax.get(clock),
            restricted_fmax_mhz=restricted.get(clock),
            slack=slack.get(clock, {}),
            tns=tns.get(clock, {}),
            met=all(v >= 0 for v in slack.get(clock, {}).values()),
        )
        for clock in clocks
    ]


def _keep_worst(store: dict[str, dict[str, float]], clock: str, check: str, value: float) -> None:
    """_keep_worst - remember the lowest value seen for this clock and check."""
    per_clock = store.setdefault(clock, {})
    if check not in per_clock or value < per_clock[check]:
        per_clock[check] = value


def _fmax(report: str) -> tuple[dict[str, float], dict[str, float]]:
    """_fmax - the lowest Fmax per clock across the slow corners

    Only the slow corners have an Fmax panel, which is why a fast-corner entry
    never appears and fmax stays None for a design nobody ran timing on.
    """
    fmax: dict[str, float] = {}
    restricted: dict[str, float] = {}

    inside = False
    for line in report.splitlines():
        if _FMAX_HEADER.match(line):
            inside = True
            continue
        if not inside:
            continue

        row = _FMAX_ROW.match(line)
        if row:
            clock = row["clock"]
            value = float(row["fmax"])
            if clock not in fmax or value < fmax[clock]:
                fmax[clock] = value
                restricted[clock] = float(row["restricted"])
        elif line.startswith(";") and "Fmax" not in line:
            inside = False

    return fmax, restricted


def _messages(out: Path, revision: str) -> tuple[list[Message], int]:
    """_messages - warnings and errors from every report, worst first

    The same message often appears in several reports, so identical ones are
    folded together and attributed to the first report that carried them.
    """
    seen: dict[tuple[str, str, str], Message] = {}
    for path in sorted(out.glob(f"{revision}.*.rpt")):
        text = _read(path)
        if text is None:
            continue
        for line in text.splitlines():
            m = _MESSAGE.match(line)
            if m is None:
                continue
            key = (m["severity"], m["code"], m["text"])
            seen.setdefault(
                key,
                Message(severity=m["severity"], code=m["code"], text=m["text"], source=path.name),
            )

    ordered = sorted(seen.values(), key=lambda x: (SEVERITY_RANK.get(x.severity, 9), x.code))
    return ordered[:MAX_MESSAGES], len(ordered)
