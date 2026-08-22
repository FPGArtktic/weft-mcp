# SPDX-License-Identifier: GPL-3.0-only
"""Behavioural simulation.

The simulator follows from the sources' language: GHDL for VHDL, Verilator or
Icarus for Verilog and SystemVerilog. None of the three reads more than one
language, so a mixed-language source set is narrowed to the testbench's
language, or refused when there is no testbench to narrow by.

Questa is the exception, and the reason it is here: it reads all three in one
simulation, so a mixed-language hierarchy runs whole rather than a module at a
time. It is proprietary and licensed and runs on the host, so it is never the
default -- a caller asks for it by name, and the configuration must say where
it is.
"""

import shlex
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .. import podman
from ..sandbox import container_path, resolve
from . import questa

VERILOG = "verilog"
VHDL = "vhdl"

VERILATOR = "verilator"
ICARUS = "icarus"
GHDL = "ghdl"
QUESTA = "questa"

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

#: Simulators that read both languages in one run, and so exclude nothing.
BILINGUAL = {QUESTA}

SIMULATORS = sorted(SIMULATOR_LANGUAGE | dict.fromkeys(BILINGUAL))

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
    @excluded: sources left out because they are written in the other
               language; empty for a single-language run
    """

    passed: bool
    returncode: int
    simulator: str
    log: str
    waveform: str | None
    excluded: list[str]


def simulate(
    image: str,
    workspace: Path,
    files: list[str],
    top: str,
    testbench: str | None = None,
    simulator: str | None = None,
    timeout: float | None = None,
    log_lines: int = DEFAULT_LOG_LINES,
    install: "questa.Install | None" = None,
) -> Simulation:
    """simulate - build and run a testbench

    @image: weft-tools image name
    @workspace: sandbox root; every path resolves inside it
    @files: design sources
    @top: unit to elaborate and run, normally the testbench module or entity
    @testbench: testbench source, appended to @files; may be None when @files
                already contains it
    @simulator: "verilator", "icarus", "ghdl" or "questa"; defaults to the
                one that fits the sources' language. Questa is never a default
                -- it is proprietary and licensed, and must be asked for.
    @timeout: wall-clock limit in seconds
    @log_lines: how many trailing output lines to return
    @install: the probed Questa installation, needed only for that simulator

    Pass or fail comes from the exit status, which is the only signal every
    simulator agrees on. A testbench that reports its verdict by printing and
    then finishing normally counts as passed; its own words are in @log.

    A source set spanning both languages is not refused when @testbench names
    one of them: the testbench decides which language runs, the rest is left
    out, and the excluded files are named in the result. That is how a single
    module of a mixed-language design gets simulated.

    Return: a Simulation describing the run.

    Raises SimulationError for an empty source set, a set spanning both
    languages with no testbench to choose between them, an unknown simulator,
    or a simulator that cannot read the sources given; SandboxError if a path
    escapes the workspace; PodmanError if the container could not start.
    """
    sources = list(files) + ([testbench] if testbench else [])
    if not sources:
        raise SimulationError("no sources to simulate")

    tool, kept, excluded = _plan(sources, testbench, simulator)
    waves = _wave_dir(workspace)
    started = time.time()

    if tool == QUESTA:
        result = _questa(install, workspace, kept, top, waves, timeout)
        held = questa.passed(result.returncode)
    else:
        paths = [str(container_path(workspace, s)) for s in kept]
        script = _script(tool, top, paths, container_path(workspace, waves))
        result = podman.run(image, workspace, ["sh", "-c", script], timeout=timeout)
        held = result.returncode == 0

    return Simulation(
        passed=held,
        returncode=result.returncode,
        simulator=tool,
        log=_tail(result.output, log_lines),
        waveform=_waveform(workspace, waves, started),
        excluded=excluded,
    )


def _questa(
    install: "questa.Install | None",
    workspace: Path,
    sources: list[str],
    top: str,
    waves: Path,
    timeout: float | None,
) -> podman.Result:
    """_questa - run the host simulator over resolved host paths

    The work library is built in a temporary directory and thrown away. It
    costs a recompile every run, which for a fast loop is a second, and it
    removes the whole class of failure where a stale library from an earlier
    source tree is elaborated instead of the sources just given.

    Raises SimulationError when no Questa is configured.
    """
    if install is None:
        raise SimulationError(
            "no Questa configured; add a [questa] section naming its root, or "
            "use one of the containerised simulators"
        )

    verilog, vhdl = [], []
    for source in sources:
        target = vhdl if SUFFIX_LANGUAGE[Path(source).suffix.lower()] == VHDL else verilog
        target.append(resolve(workspace, source))

    with tempfile.TemporaryDirectory(prefix="weft-questa-") as scratch:
        return questa.run(
            install,
            Path(scratch),
            verilog,
            vhdl,
            top,
            waveform=resolve(workspace, waves) / f"{top}.vcd",
            timeout=timeout,
        )


def _plan(
    sources: list[str], testbench: str | None, simulator: str | None
) -> tuple[str, list[str], list[str]]:
    """_plan - decide what runs, in which simulator, over which sources

    @sources: every file the caller offered, testbench included
    @testbench: the testbench source, or None
    @simulator: the simulator asked for by name, or None to infer one

    Whether a source set has to be narrowed is a property of the simulator,
    not of simulation. Verilator, Icarus and GHDL each read one language, so a
    set spanning both must lose half; Questa reads both, so it loses nothing
    and the hierarchy runs whole. Deciding the tool first and the sources
    afterwards is what lets the same function say both.

    Where the tool is inferred, the language decides it, and a mixed set needs
    a testbench to say which half is meant. Guessing would silently simulate
    the wrong one.

    Return: the simulator, the sources to hand it, and the sources left out.

    Raises SimulationError for an unrecognised suffix, an unknown simulator,
    a simulator that cannot read any of the sources, or a mixed set with
    neither a testbench nor a simulator to resolve it.
    """
    found: dict[str, list[str]] = {}
    for s in sources:
        language = SUFFIX_LANGUAGE.get(Path(s).suffix.lower())
        if language is None:
            raise SimulationError(f"unrecognised source type: {s}")
        found.setdefault(language, []).append(s)

    if simulator is not None:
        return _named(simulator.lower(), found, sources)

    if len(found) == 1:
        language, kept = next(iter(found.items()))
        return DEFAULT_SIMULATOR[language], kept, []

    if testbench is None:
        listing = "; ".join(f"{lang}: {', '.join(fs)}" for lang, fs in sorted(found.items()))
        raise SimulationError(
            f"source set spans both languages ({listing}). Of the containerised "
            "simulators none reads both in one run; name a testbench to pick the "
            "language, or name questa to simulate the design whole"
        )

    language = SUFFIX_LANGUAGE[Path(testbench).suffix.lower()]
    return DEFAULT_SIMULATOR[language], found[language], _others(found, language)


def _named(
    tool: str, found: dict[str, list[str]], sources: list[str]
) -> tuple[str, list[str], list[str]]:
    """_named - honour the simulator the caller asked for by name."""
    if tool in BILINGUAL:
        return tool, sources, []

    language = SIMULATOR_LANGUAGE.get(tool)
    if language is None:
        raise SimulationError(f"unknown simulator: {tool}; expected one of {SIMULATORS}")
    if language not in found:
        raise SimulationError(f"{tool} does not read {'/'.join(sorted(found))} sources")
    return tool, found[language], _others(found, language)


def _others(found: dict[str, list[str]], language: str) -> list[str]:
    """_others - the sources in the language that is not being simulated."""
    return [s for lang, fs in sorted(found.items()) if lang != language for s in fs]


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
