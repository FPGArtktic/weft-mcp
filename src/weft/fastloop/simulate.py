# SPDX-License-Identifier: GPL-2.0-only
"""Behavioural simulation inside weft-tools.

The simulator follows from the sources' language: GHDL for VHDL, Verilator or
Icarus for Verilog and SystemVerilog. None of the three reads more than one
language, so a mixed-language source set is refused with an explanation rather
than handed to a tool that will fail obscurely.
"""

import shlex
import time
from dataclasses import dataclass
from pathlib import Path

from .. import podman
from ..sandbox import container_path, resolve

VERILOG = "verilog"
VHDL = "vhdl"

VERILATOR = "verilator"
ICARUS = "icarus"
GHDL = "ghdl"

SUFFIX_LANGUAGE = {
    ".v": VERILOG,
    ".sv": VERILOG,
    ".svh": VERILOG,
    ".vh": VERILOG,
    ".vhd": VHDL,
    ".vhdl": VHDL,
}

#: Verilator handles the SystemVerilog a modern testbench actually uses, so it
#: is the default; Icarus stays available for designs it suits better.
DEFAULT_SIMULATOR = {VERILOG: VERILATOR, VHDL: GHDL}

SIMULATOR_LANGUAGE = {VERILATOR: VERILOG, ICARUS: VERILOG, GHDL: VHDL}

#: Build products stay out of the workspace; only waveforms go back.
BUILD_DIR = "/tmp/weft-build"

#: Waveforms land here, inside the workspace, so a client can fetch them.
WAVE_DIR = Path(".weft") / "waves"

WAVE_SUFFIXES = (".vcd", ".fst")

DEFAULT_LOG_LINES = 80


class SimulationError(ValueError):
    """The request cannot be simulated as stated."""


@dataclass(frozen=True)
class Simulation:
    """Outcome of one simulation run.

    @passed: the simulator and the design finished without an error status
    @returncode: exit status of the simulation
    @simulator: which of verilator, icarus, ghdl actually ran
    @log: tail of the combined build and run output
    @waveform: workspace-relative path to a waveform, or None if none was
               written; only GHDL is told where to write one, the Verilog
               simulators write one only if the testbench dumps
    """

    passed: bool
    returncode: int
    simulator: str
    log: str
    waveform: str | None


def simulate(
    image: str,
    workspace: Path,
    files: list[str],
    top: str,
    testbench: str | None = None,
    simulator: str | None = None,
    timeout: float | None = None,
    log_lines: int = DEFAULT_LOG_LINES,
) -> Simulation:
    """simulate - build and run a testbench

    @image: weft-tools image name
    @workspace: sandbox root; every path resolves inside it
    @files: design sources
    @top: unit to elaborate and run, normally the testbench module or entity
    @testbench: testbench source, appended to @files; may be None when @files
                already contains it
    @simulator: "verilator", "icarus" or "ghdl"; defaults to the one that fits
                the sources' language
    @timeout: wall-clock limit in seconds
    @log_lines: how many trailing output lines to return

    Pass or fail comes from the exit status, which is the only signal every
    simulator agrees on. A testbench that reports its verdict by printing and
    then finishing normally counts as passed; its own words are in @log.

    Return: a Simulation describing the run.

    Raises SimulationError for an empty or mixed-language source set, an
    unknown simulator, or a simulator that cannot read the sources given;
    SandboxError if a path escapes the workspace; PodmanError if the container
    could not start.
    """
    sources = list(files) + ([testbench] if testbench else [])
    if not sources:
        raise SimulationError("no sources to simulate")

    language = _language(sources)
    tool = _tool(simulator, language)

    paths = [str(container_path(workspace, s)) for s in sources]
    waves = _wave_dir(workspace)
    started = time.time()

    script = _script(tool, top, paths, container_path(workspace, waves))
    result = podman.run(image, workspace, ["sh", "-c", script], timeout=timeout)

    return Simulation(
        passed=result.returncode == 0,
        returncode=result.returncode,
        simulator=tool,
        log=_tail(result.output, log_lines),
        waveform=_waveform(workspace, waves, started),
    )


def _language(sources: list[str]) -> str:
    """_language - decide which language the source set is written in.

    A mixed set is an error: no open simulator reads both, and saying so is
    more useful than letting one of them fail on a file it cannot parse.
    """
    found = {}
    for s in sources:
        suffix = Path(s).suffix.lower()
        language = SUFFIX_LANGUAGE.get(suffix)
        if language is None:
            raise SimulationError(f"unrecognised source type: {s}")
        found.setdefault(language, []).append(s)

    if len(found) > 1:
        raise SimulationError(
            "mixed-language source set: "
            + "; ".join(f"{lang}: {', '.join(fs)}" for lang, fs in sorted(found.items()))
            + ". No open simulator reads both Verilog and VHDL in one run"
        )
    return next(iter(found))


def _tool(simulator: str | None, language: str) -> str:
    """_tool - pick the simulator, or check the one the caller asked for."""
    if simulator is None:
        return DEFAULT_SIMULATOR[language]

    wanted = SIMULATOR_LANGUAGE.get(simulator.lower())
    if wanted is None:
        raise SimulationError(
            f"unknown simulator: {simulator}; expected one of {sorted(SIMULATOR_LANGUAGE)}"
        )
    if wanted != language:
        raise SimulationError(f"{simulator} does not read {language} sources")
    return simulator.lower()


def _script(tool: str, top: str, paths: list[str], waves: Path) -> str:
    """_script - the shell pipeline that builds and then runs the testbench.

    The run happens with the waveform directory as working directory, so a
    testbench that calls $dumpfile with a relative name writes where the
    caller can find the result.
    """
    q = shlex.quote
    build = q(BUILD_DIR)
    sources = " ".join(q(p) for p in paths)
    lines = ["set -e", f"mkdir -p {build} {q(str(waves))}"]

    if tool == VERILATOR:
        # Verilator makes warnings fatal by default. Reporting them is lint's
        # job; a style complaint must not stop a simulation from running.
        lines += [
            f"verilator --binary --timing --trace -Wno-fatal --Mdir {build} -o sim "
            f"--top-module {q(top)} {sources}",
            f"cd {q(str(waves))}",
            f"exec {build}/sim",
        ]
    elif tool == ICARUS:
        lines += [
            f"iverilog -g2012 -o {build}/sim -s {q(top)} {sources}",
            f"cd {q(str(waves))}",
            f"exec vvp {build}/sim",
        ]
    else:
        lines += [
            f"ghdl -a --std=08 --workdir={build} {sources}",
            f"cd {q(str(waves))}",
            f"exec ghdl -r --std=08 --workdir={build} {q(top)} --vcd={q(top + '.vcd')}",
        ]

    return "\n".join(lines)


def _wave_dir(workspace: Path) -> Path:
    """_wave_dir - workspace-relative directory that collects waveforms."""
    resolve(workspace, WAVE_DIR).mkdir(parents=True, exist_ok=True)
    return WAVE_DIR


def _waveform(workspace: Path, waves: Path, since: float) -> str | None:
    """_waveform - the waveform this run produced, if it produced one.

    Files are matched by modification time rather than by name, because the
    Verilog simulators let the testbench choose the name.
    """
    directory = resolve(workspace, waves)
    fresh = [
        p
        for p in directory.iterdir()
        if p.suffix.lower() in WAVE_SUFFIXES and p.stat().st_mtime >= since
    ]
    if not fresh:
        return None
    newest = max(fresh, key=lambda p: p.stat().st_mtime)
    return str(waves / newest.name)


def _tail(text: str, lines: int) -> str:
    """_tail - the last @lines lines, so a long log does not flood the client."""
    kept = text.splitlines()[-lines:]
    return "\n".join(kept)
