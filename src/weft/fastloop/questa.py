# SPDX-License-Identifier: GPL-3.0-only
"""Simulation in Questa, on the host.

Questa - Altera Starter FPGA Edition ships beside Quartus. It is proprietary
and licensed, so it lives on the host like Quartus does rather than in
weft-tools, and it is used only where the configuration names it.

It earns its place by reading Verilog, SystemVerilog and VHDL in one
simulation. No open simulator does, so a mixed-language design can otherwise
only be run a module at a time, with the other language's blocks left out --
which is exactly the part a hierarchy test is about.

The verdict does not come from the exit status. `vsim -c` exits 0 after a
$fatal, so a run that stopped on a failed assertion looks identical to one
that passed. Questa records what happened in TESTSTATUS instead, and this
reads it and hands it back as the exit code.
"""

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..podman import Result

#: TESTSTATUS as Questa defines it: 0 clean, 1 warning, 2 error, 3 fatal.
STATUS_WARNING = 1

#: Signals are optimised away by default, and a waveform of nothing is worse
#: than no waveform: it looks like the design was silent.
ACCESS = "+acc"

VHDL_STANDARD = "2008"

VERSION_TIMEOUT = 60

#: The ini-file note every invocation prints. It is about Questa's own
#: configuration, not about the sources, and it would be the first line of
#: every lint result.
NOISE = ("vlog-220", "vcom-220", "vsim-220")


class QuestaError(RuntimeError):
    """Questa is missing, unlicensed, or could not be started."""


@dataclass(frozen=True)
class Install:
    """A probed Questa installation.

    @root: install root
    @version: the banner vsim prints, for reporting
    @env: extra environment every invocation gets
    """

    root: Path
    version: str
    env: dict[str, str]


def probe(root: Path, env: dict[str, str] | None = None) -> Install:
    """probe - identify the installation at @root

    @root: install root, the directory holding bin/vsim
    @env: extra environment, normally the licence variable

    Return: an Install carrying the version banner.

    Raises QuestaError if vsim is absent or will not report its version, which
    is what an unlicensed installation does.
    """
    environment = dict(env or {})
    binary = Path(root) / "bin" / "vsim"
    try:
        done = subprocess.run(
            [str(binary), "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=VERSION_TIMEOUT,
            env={**_environ(), **environment},
            check=False,
        )
    except FileNotFoundError as e:
        raise QuestaError(f"no vsim at {binary}") from e
    except subprocess.TimeoutExpired as e:
        raise QuestaError(f"vsim did not answer -version within {VERSION_TIMEOUT}s") from e

    if done.returncode != 0:
        raise QuestaError(f"vsim -version failed: {' '.join(done.stdout.split())[:200]}")

    return Install(root=Path(root), version=done.stdout.strip().splitlines()[0], env=environment)


def run(
    install: Install,
    directory: Path,
    verilog: list[Path],
    vhdl: list[Path],
    top: str,
    waveform: Path | None = None,
    timeout: float | None = None,
) -> Result:
    """run - compile and simulate one testbench

    @install: the probed installation
    @directory: scratch directory for the work library and the do file; it is
                the working directory of every step, so nothing is written
                beside the sources
    @verilog: Verilog and SystemVerilog sources
    @vhdl: VHDL sources
    @top: unit to elaborate
    @waveform: where to write a VCD, or None for no waveform
    @timeout: wall-clock limit for the whole run

    VHDL is compiled first. Questa resolves across languages at elaboration,
    not at compile time, so the order is not strictly required -- it is the
    order that gives a readable transcript when an entity is missing.

    Return: a Result whose returncode is Questa's TESTSTATUS, so 0 is a clean
    run, 1 carries warnings, 2 saw an error and 3 stopped on a fatal.

    Raises QuestaError if a step could not be started at all, and
    subprocess.TimeoutExpired if @timeout elapses.
    """
    transcript: list[str] = []

    steps = [["vlib", "work"]]
    if vhdl:
        steps.append(["vcom", f"-{VHDL_STANDARD}", *[str(p) for p in vhdl]])
    if verilog:
        steps.append(["vlog", "-sv", *[str(p) for p in verilog]])

    for argv in steps:
        done = _tool(install, directory, argv, timeout)
        transcript.append(done.output)
        if done.returncode != 0:
            return Result(done.returncode, "\n".join(transcript))

    script = directory / "weft.do"
    script.write_text(_script(waveform))
    done = _tool(
        install,
        directory,
        ["vsim", "-c", "-onfinish", "stop", f"-voptargs={ACCESS}", "-do", script.name, top],
        timeout,
    )
    transcript.append(done.output)
    return Result(done.returncode, "\n".join(transcript))


def passed(returncode: int) -> bool:
    """passed - whether a TESTSTATUS means the testbench held

    A warning does not fail a run. Questa warns about things a working design
    does routinely -- an unconnected optional port, a signal read before its
    first assignment during reset -- and failing on those would make the fast
    loop useless. An error or a fatal fails it.
    """
    return returncode <= STATUS_WARNING


def _script(waveform: Path | None) -> str:
    """_script - the do file that runs the simulation and reports its verdict.

    TESTSTATUS is read after the run rather than trusted to the exit status,
    and -onfinish stop is what makes that possible: $finish would otherwise
    end vsim on the spot and the lines below would never execute.
    """
    lines = []
    if waveform is not None:
        lines += [f"vcd file {_tcl(str(waveform))}", "vcd add -r /*"]
    lines += [
        "run -all",
        "set status [coverage attribute -name TESTSTATUS -concise]",
        "quit -f -code $status",
    ]
    return "\n".join(lines) + "\n"


def _tcl(value: str) -> str:
    """_tcl - a path as one Tcl word

    Braces and backslashes are refused rather than escaped. Tcl quoting has
    several forms that each escape a different set, and a path that needs one
    of them is a path nobody meant to write.

    Raises QuestaError for a path Tcl cannot be handed safely.
    """
    if any(c in value for c in "{}\\\n"):
        raise QuestaError(f"path cannot be passed to Questa: {value}")
    return "{" + value + "}"


def _tool(install: Install, directory: Path, argv: list[str], timeout: float | None) -> Result:
    """_tool - run one Questa executable in @directory."""
    binary = install.root / "bin" / argv[0]
    try:
        done = subprocess.run(
            [str(binary), *argv[1:]],
            cwd=directory,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=timeout,
            env={**_environ(), **install.env},
            check=False,
        )
    except FileNotFoundError as e:
        raise QuestaError(f"no {argv[0]} at {binary}") from e

    return Result(done.returncode, done.stdout)


def _environ() -> dict[str, str]:
    """_environ - the server's own environment, which the tools inherit."""
    return dict(os.environ)


def check(
    install: Install,
    workspace: Path,
    scratch: Path,
    verilog: list[str],
    vhdl: list[str],
    timeout: float | None = None,
) -> Result:
    """check - compile the sources without simulating them

    @install: the probed installation
    @workspace: sandbox root, and the working directory of every step, so the
                diagnostics name the paths the caller gave
    @scratch: directory for the work library, kept out of the workspace
    @verilog: Verilog and SystemVerilog sources, workspace-relative
    @vhdl: VHDL sources, workspace-relative
    @timeout: wall-clock limit for the whole check

    This is a compile check with the vendor's own front-end, not a style
    linter: full lint is a licensed Questa feature the Starter Edition does
    not carry. What it does give is the front-end Questa itself will use --
    it accepts SystemVerilog Verilator refuses and refuses what Verilator
    wrongly accepts -- and it reads both languages in one pass, so a VHDL
    entity instantiated from SystemVerilog is actually resolved.

    Return: a Result carrying the combined transcript. The exit status is the
    worst of the steps; the diagnostics in the transcript are what matters.

    Raises QuestaError if a step could not be started at all.
    """
    library = scratch / "work"
    transcript: list[str] = []
    worst = 0

    made = _tool(install, workspace, ["vlib", str(library)], timeout)
    transcript.append(made.output)
    if made.returncode != 0:
        return Result(made.returncode, "\n".join(transcript))

    steps = []
    if vhdl:
        steps.append(["vcom", f"-{VHDL_STANDARD}", "-lint", "-work", str(library), *vhdl])
    if verilog:
        steps.append(["vlog", "-sv", "-lint", "-work", str(library), *verilog])

    for argv in steps:
        done = _tool(install, workspace, argv, timeout)
        transcript.append(done.output)
        worst = max(worst, done.returncode)

    return Result(worst, "\n".join(transcript))
