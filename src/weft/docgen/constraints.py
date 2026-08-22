# SPDX-License-Identifier: GPL-3.0-only
"""Pin assignments and clock constraints, read from the project's own files.

Two sources answer "which pin is this signal on", and they do not always
agree: the .qsf holds what the designer asked for, the fitter's .pin report
holds where the signal actually went. The report wins when it exists, because
a document showing a requested pin for a design fitted elsewhere would be
wrong in the one way a pin map must never be wrong.
"""

import re
from dataclasses import dataclass
from pathlib import Path

#: A row of the fitted pin report: name, location, direction, I/O standard,
#: voltage, bank, user assignment -- colon separated, padded with spaces.
_PIN_ROW = re.compile(r"^(?P<name>\S(?:[^:]*\S)?)\s*:\s*(?P<rest>.*)$")

#: Device pins the fitter lists alongside the design's own. Both prefixes are
#: Quartus's, not a guess: dedicated pins are written ~ALTERA_TCK~ and unused
#: ones RESERVED_INPUT_WITH_WEAK_PULLUP.
_NOT_USER_PINS = ("~", "RESERVED")

_DIRECTIONS = ("input", "output", "bidir")

#: set_location_assignment PIN_27 -to clk
_LOCATION = re.compile(r"^\s*set_location_assignment\s+(?P<pin>\S+)\s+-to\s+(?P<signal>\S+)", re.M)

#: set_instance_assignment -name IO_STANDARD "3.3-V LVTTL" -to clk
_INSTANCE = re.compile(
    r"^\s*set_instance_assignment\s+-name\s+(?P<name>\S+)\s+(?P<value>\"[^\"]*\"|\S+)\s+-to\s+"
    r"(?P<signal>\S+)",
    re.M,
)

#: create_clock -name clk -period 20.000 [get_ports {clk}]
_CREATE_CLOCK = re.compile(
    r"^\s*(?P<kind>create_clock|create_generated_clock)\s+(?P<args>.*)$", re.M
)

_NAME_ARG = re.compile(r"-name\s+(?P<name>\{[^}]*\}|\S+)")
_PERIOD_ARG = re.compile(r"-period\s+(?P<period>[\d.]+)")
_TARGET_ARG = re.compile(r"\[\s*get_ports\s+(?P<target>\{[^}]*\}|\S+?)\s*\]")

#: SDC files the project declares.
_SDC_FILE = re.compile(r"^\s*set_global_assignment\s+-name\s+SDC_FILE\s+(?P<path>\S+)", re.M)


@dataclass(frozen=True)
class Pin:
    """One signal and the pin it sits on.

    @signal: the port name, bit index included where the port is a vector
    @location: pin number or name as the device labels it
    @direction: "input", "output" or "bidir"
    @io_standard: the electrical standard, when one is recorded
    @bank: I/O bank, when the report gives one
    @assigned: True when the designer pinned it, False when the fitter chose
    """

    signal: str
    location: str
    direction: str | None = None
    io_standard: str | None = None
    bank: str | None = None
    assigned: bool = False


@dataclass(frozen=True)
class Clock:
    """One clock the constraints define.

    @name: clock name as the constraint spells it
    @period_ns: the constrained period
    @frequency_mhz: the same, expressed the way a datasheet would
    @target: the port or node the clock is attached to, when the constraint
             names one
    @generated: True for create_generated_clock, which is derived from another
    @file: the .sdc it came from, relative to the project directory
    """

    name: str
    period_ns: float | None
    frequency_mhz: float | None
    target: str | None
    generated: bool
    file: str


def pins(project_dir: Path, revision: str, output_dir: Path | None = None) -> list[Pin]:
    """pins - the design's pin map

    @project_dir: the project directory
    @revision: revision name
    @output_dir: where the fitter's reports are, when a compilation exists

    The fitted report is preferred over the .qsf. A .qsf location says what
    was asked for; the report says where the signal went, and for a design
    with no pin constraints at all -- which is every design before it meets a
    board -- the .qsf says nothing while the report says everything.

    Return: one Pin per signal, sorted by signal name. Empty when the design
    has neither constraints nor a fitted report.
    """
    project_dir = Path(project_dir)

    if output_dir is not None:
        report = Path(output_dir) / f"{revision}.pin"
        found = _fitted(report, _standards(project_dir, revision))
        if found:
            return found

    return _requested(project_dir, revision)


def clocks(project_dir: Path, revision: str) -> list[Clock]:
    """clocks - every clock the constraint files create

    @project_dir: the project directory
    @revision: revision name, used to find the .qsf that lists the .sdc files

    Files are taken from the project's own SDC_FILE assignments. A .sdc lying
    in the directory but not declared is not read: Quartus would not read it
    either, and documenting a constraint that never reached the tool would be
    a lie about the design.

    Return: the clocks, in the order the files declare them.
    """
    project_dir = Path(project_dir)
    found = []
    for name in _sdc_files(project_dir, revision):
        path = project_dir / name
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        found.extend(_clocks(text, name))
    return found


def _sdc_files(project_dir: Path, revision: str) -> list[str]:
    """_sdc_files - the .sdc files the .qsf declares, in order."""
    qsf = project_dir / f"{revision}.qsf"
    try:
        text = qsf.read_text(errors="replace")
    except OSError:
        return []
    return [m["path"].strip('"') for m in _SDC_FILE.finditer(text)]


def _clocks(text: str, source: str) -> list[Clock]:
    """_clocks - the create_clock lines of one .sdc."""
    found = []
    for m in _CREATE_CLOCK.finditer(_uncomment(text)):
        args = m["args"]
        name = _NAME_ARG.search(args)
        period = _PERIOD_ARG.search(args)
        target = _TARGET_ARG.search(args)

        seconds = float(period["period"]) if period else None
        found.append(
            Clock(
                name=_bare(name["name"]) if name else _bare(target["target"]) if target else "?",
                period_ns=seconds,
                frequency_mhz=round(1000.0 / seconds, 3) if seconds else None,
                target=_bare(target["target"]) if target else None,
                generated=m["kind"] == "create_generated_clock",
                file=source,
            )
        )
    return found


def _uncomment(text: str) -> str:
    """_uncomment - drop Tcl comments, so a commented-out clock is not read.

    A `;#` trailing comment ends the command, and a line opening with # is a
    comment entirely. Neither reaches the tool, so neither belongs in the
    documentation.
    """
    lines = []
    for line in text.splitlines():
        stripped = line.split(";#")[0]
        lines.append("" if stripped.lstrip().startswith("#") else stripped)
    return "\n".join(lines)


def _bare(value: str) -> str:
    """_bare - a Tcl word without its braces or quotes."""
    return value.strip().strip("{}").strip('"').strip()


def _requested(project_dir: Path, revision: str) -> list[Pin]:
    """_requested - pin locations the .qsf asks for."""
    qsf = project_dir / f"{revision}.qsf"
    try:
        text = qsf.read_text(errors="replace")
    except OSError:
        return []

    standards = _instance_values(text, "IO_STANDARD")
    found = [
        Pin(
            signal=_bare(m["signal"]),
            location=_bare(m["pin"]),
            io_standard=standards.get(_bare(m["signal"])),
            assigned=True,
        )
        for m in _LOCATION.finditer(text)
    ]
    return sorted(found, key=lambda p: _order(p.signal))


def _standards(project_dir: Path, revision: str) -> dict[str, str]:
    """_standards - IO_STANDARD per signal, from the .qsf."""
    try:
        text = (project_dir / f"{revision}.qsf").read_text(errors="replace")
    except OSError:
        return {}
    return _instance_values(text, "IO_STANDARD")


def _instance_values(text: str, assignment: str) -> dict[str, str]:
    """_instance_values - one instance assignment's value per signal."""
    return {
        _bare(m["signal"]): _bare(m["value"])
        for m in _INSTANCE.finditer(text)
        if m["name"].upper() == assignment
    }


def _fitted(report: Path, requested: dict[str, str]) -> list[Pin]:
    """_fitted - the design's own signals out of the fitter's pin report

    @report: the .pin file
    @requested: IO_STANDARD per signal from the .qsf, so a pin can be marked
                as one the designer placed rather than one the fitter chose

    Device pins are dropped. The report lists every pin the package has,
    power and JTAG included, and a pin map of the design is about the design.
    """
    try:
        text = report.read_text(errors="replace")
    except OSError:
        return []

    found = []
    for line in text.splitlines():
        row = _PIN_ROW.match(line)
        if row is None:
            continue
        name = row["name"]
        if name.startswith(_NOT_USER_PINS):
            continue

        cells = [c.strip() for c in row["rest"].split(":")]
        if len(cells) < 3 or cells[1] not in _DIRECTIONS:
            continue

        found.append(
            Pin(
                signal=name,
                location=cells[0],
                direction=cells[1],
                io_standard=cells[2] or None,
                bank=cells[4] if len(cells) > 4 and cells[4] else None,
                assigned=name in requested or (len(cells) > 5 and cells[5].upper() == "Y"),
            )
        )
    return sorted(found, key=lambda p: _order(p.signal))


def _order(signal: str) -> tuple[str, int]:
    """_order - sort led[10] after led[9] rather than after led[1]."""
    m = re.match(r"^(?P<base>.*?)\[(?P<index>\d+)\]$", signal)
    if m is None:
        return (signal, -1)
    return (m["base"], int(m["index"]))
