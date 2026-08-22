# SPDX-License-Identifier: GPL-3.0-only
"""Starting Quartus compilations as persistent jobs.

A compilation is long, so it is handed to the job store rather than waited on.
This module only decides what to run and where the log goes.
"""

import re
from pathlib import Path

from ..jobs import Job, JobStore
from .install import Install

FULL = "full"
SYN = "syn"
FIT = "fit"
ASM = "asm"
STA = "sta"

#: Stage names from PROJECT.md 4.2 mapped to the executable that runs them.
#: "syn" is deliberately absent: Pro synthesises with quartus_syn and Lite with
#: quartus_map, and both files exist in a Lite installation, so the edition has
#: to choose.
STAGE_TOOLS = {
    FIT: "quartus_fit",
    ASM: "quartus_asm",
    STA: "quartus_sta",
}

STAGES = (FULL, SYN, FIT, ASM, STA)

#: What each stage calls itself in the log. The flow announces every phase it
#: enters, so the last announcement is where the compilation has got to.
PHASE_NAMES = {
    "Analysis & Synthesis": SYN,
    "Fitter": FIT,
    "Assembler": ASM,
    "Power Analyzer": "pow",
    "Timing Analyzer": STA,
    "EDA Netlist Writer": "eda",
}

_RUNNING = re.compile(r"^Info: Running Quartus Prime (?P<phase>.+?)\s*$", re.M)

#: Logs live under the workspace state directory, never beside the sources.
LOG_DIR = Path(".weft") / "logs"


class FlowError(ValueError):
    """The compilation cannot be started as asked."""


def start_compile(
    store: JobStore,
    install: Install,
    directory: Path,
    revision: str,
    stage: str = FULL,
    logs: Path | None = None,
) -> Job:
    """start_compile - run a stage, or the whole flow, in the background

    @store: job store that will own the running process
    @install: the Quartus installation to drive
    @directory: the project directory, which becomes the working directory
    @revision: revision name, which is also the project basename
    @stage: "full", "syn", "fit", "asm" or "sta"
    @logs: directory for the log file; defaults to the project's own

    A single stage runs its own executable directly rather than through a
    flow, so nothing else is re-run on the way. Quartus is invoked with the
    project directory as its working directory and a bare revision name, which
    keeps every path inside the project relative.

    Return: the Job, already running.

    Raises FlowError for an unknown stage or a missing project, and
    InstallError if the executable this edition needs is absent.
    """
    if stage not in STAGES:
        raise FlowError(f"unknown stage: {stage}; expected one of {STAGES}")

    directory = Path(directory)
    if not (directory / f"{revision}.qpf").is_file():
        raise FlowError(f"no project {revision} in {directory}")

    argv = _argv(install, revision, stage)

    log_dir = Path(logs) if logs else directory / LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)

    return store.start(
        argv=argv,
        cwd=directory,
        project=str(directory / f"{revision}.qpf"),
        revision=revision,
        flow=stage,
        log_path=log_dir / f"{revision}-{stage}.log",
        env=install.env,
    )


def progress(job: Job) -> str | None:
    """progress - which phase the compilation has reached

    @job: a job started by start_compile

    Read from the log rather than tracked in the database, because the phase
    is the tool's business and the log is where the tool says so.

    Return: the stage name of the last phase announced, or None if the tool
    has not announced one yet or the log cannot be read.
    """
    try:
        text = Path(job.log_path).read_text(errors="replace")
    except OSError:
        return None

    seen = _RUNNING.findall(text)
    for phase in reversed(seen):
        if phase in PHASE_NAMES:
            return PHASE_NAMES[phase]
    return None


def _argv(install: Install, revision: str, stage: str) -> list[str]:
    """_argv - the command line for one stage, or for the whole flow."""
    if stage == FULL:
        return [str(install.require("quartus_sh")), "--flow", "compile", revision, "-c", revision]

    tool = install.synthesis_tool if stage == SYN else STAGE_TOOLS[stage]
    return [str(install.require(tool)), revision, "-c", revision]
