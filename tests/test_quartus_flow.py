# SPDX-License-Identifier: GPL-3.0-only
"""Tests for starting compilations.

No Quartus runs here. The command line that would have been executed is
inspected instead, and the progress reader is fed log text captured from a
real 25.1std compile.
"""

import pytest

from weft.jobs import Job
from weft.quartus.flow import (
    FULL,
    STAGES,
    FlowError,
    _argv,
    progress,
    start_compile,
)
from weft.quartus.install import LITE, PRO, Install

LOG = """\
Info: Running Quartus Prime Shell
Info: Running Quartus Prime Analysis & Synthesis
Info: Quartus Prime Analysis & Synthesis was successful. 0 errors, 0 warnings
Info: Running Quartus Prime Fitter
Info: Quartus Prime Fitter was successful. 0 errors, 4 warnings
Info: Running Quartus Prime Assembler
Info: Running Quartus Prime Power Analyzer
Info: Running Quartus Prime Timing Analyzer
"""


def install_at(tmp_path, edition=LITE):
    """install_at - an installation whose executables exist but do nothing."""
    binaries = tmp_path / "q" / "bin"
    binaries.mkdir(parents=True, exist_ok=True)
    for name in (
        "quartus_sh",
        "quartus_map",
        "quartus_syn",
        "quartus_fit",
        "quartus_asm",
        "quartus_sta",
    ):
        (binaries / name).write_text("")
    return Install(root=tmp_path / "q", edition=edition, version="25.1std.0", build="1129")


def project_at(tmp_path, revision="demo"):
    directory = tmp_path / "proj"
    directory.mkdir(exist_ok=True)
    (directory / f"{revision}.qpf").write_text("")
    return directory


def test_full_flow_goes_through_quartus_sh(tmp_path):
    argv = _argv(install_at(tmp_path), "demo", FULL)
    assert argv[1:] == ["--flow", "compile", "demo", "-c", "demo"]
    assert argv[0].endswith("quartus_sh")


def test_a_single_stage_runs_its_own_executable(tmp_path):
    """Running one stage through the flow would re-run the others."""
    argv = _argv(install_at(tmp_path), "demo", "fit")
    assert argv[0].endswith("quartus_fit")
    assert argv[1:] == ["demo", "-c", "demo"]


def test_lite_synthesises_with_quartus_map(tmp_path):
    assert _argv(install_at(tmp_path, LITE), "demo", "syn")[0].endswith("quartus_map")


def test_pro_synthesises_with_quartus_syn(tmp_path):
    assert _argv(install_at(tmp_path, PRO), "demo", "syn")[0].endswith("quartus_syn")


def test_every_stage_has_a_command(tmp_path):
    install = install_at(tmp_path)
    for stage in STAGES:
        assert _argv(install, "demo", stage)


def test_unknown_stage_is_refused(tmp_path):
    with pytest.raises(FlowError, match="unknown stage"):
        start_compile(None, install_at(tmp_path), project_at(tmp_path), "demo", stage="magic")


def test_missing_project_is_refused(tmp_path):
    with pytest.raises(FlowError, match="no project demo"):
        start_compile(None, install_at(tmp_path), tmp_path, "demo")


def test_the_job_is_handed_to_the_store(tmp_path):
    """start_compile decides what to run; the store owns the process."""
    seen = {}

    class FakeStore:
        def start(self, **kwargs):
            seen.update(kwargs)
            return "job"

    directory = project_at(tmp_path)
    assert start_compile(FakeStore(), install_at(tmp_path), directory, "demo") == "job"
    assert seen["cwd"] == directory
    assert seen["revision"] == "demo"
    assert seen["flow"] == FULL
    assert seen["log_path"].parent == directory / ".weft" / "logs"
    assert seen["log_path"].parent.is_dir()


def job_with(log_path):
    return Job(
        id="x",
        project="p",
        revision="demo",
        flow=FULL,
        log_path=str(log_path),
        marker_path=f"{log_path}.exit",
        pid=None,
        pid_start=None,
        status="running",
        exit_code=None,
        created_at=0.0,
        finished_at=None,
        cancelled_at=None,
    )


def test_progress_reports_the_last_phase_announced(tmp_path):
    log = tmp_path / "c.log"
    log.write_text(LOG)
    assert progress(job_with(log)) == "sta"


def test_progress_follows_the_compile(tmp_path):
    log = tmp_path / "c.log"
    lines = LOG.splitlines(keepends=True)
    log.write_text("".join(lines[:2]))
    assert progress(job_with(log)) == "syn"
    log.write_text("".join(lines[:4]))
    assert progress(job_with(log)) == "fit"


def test_progress_is_none_before_the_first_phase(tmp_path):
    log = tmp_path / "c.log"
    log.write_text("Info: Running Quartus Prime Shell\n")
    assert progress(job_with(log)) is None


def test_progress_survives_a_missing_log(tmp_path):
    assert progress(job_with(tmp_path / "absent.log")) is None
