# SPDX-License-Identifier: GPL-3.0-only
"""Tests for the persistent job store.

These use real processes rather than mocks: what is being tested is exactly
the behaviour of process ids, signals and marker files across a restart, and a
mock of those would only test itself.
"""

import os
import signal
import time
from pathlib import Path

import pytest

from weft.jobs import (
    CANCELLED,
    DONE,
    FAILED,
    LOST,
    RUNNING,
    JobError,
    JobStore,
)

TIMEOUT = 10.0


@pytest.fixture
def store(tmp_path):
    """store - a job store that does not leave its children behind.

    Several tests start `sleep 60` on purpose. Without this they outlive the
    run: a CI runner reports them as orphans it had to terminate, and a
    developer gets a handful of stray processes per test session.
    """
    s = JobStore(tmp_path / "state" / "jobs.sqlite")
    yield s
    for job in s.list():
        if job.status == RUNNING:
            s.cancel(job.id)
    s.close()


def run(store, tmp_path, argv, name="job"):
    """run - start a job with its log inside the temporary directory."""
    return store.start(
        argv=argv,
        cwd=tmp_path,
        project=str(tmp_path / "demo.qpf"),
        revision="demo",
        flow="compile",
        log_path=tmp_path / f"{name}.log",
    )


def settle(store, job_id, wanted=None):
    """settle - wait for the job to leave the running state."""
    deadline = time.time() + TIMEOUT
    while time.time() < deadline:
        job = store.get(job_id)
        if job.status != RUNNING and (wanted is None or job.status == wanted):
            return job
        time.sleep(0.05)
    return store.get(job_id)


def test_successful_job_ends_succeeded(store, tmp_path):
    job = run(store, tmp_path, ["/bin/true"])
    assert settle(store, job.id).status == DONE


def test_a_job_that_is_still_running_says_so(store, tmp_path):
    """Deliberately a command that outlives the call.

    start() ends by reading the job back, and reading it reconciles: a tool
    that has already exited is reported as finished there and then. Asserting
    RUNNING after /bin/true therefore races the process, and lost about one CI
    run in three -- the store was right and the test was wrong.
    """
    job = run(store, tmp_path, ["sleep", "60"])
    assert job.status == RUNNING
    assert store.get(job.id).status == RUNNING


def test_failing_job_keeps_its_exit_code(store, tmp_path):
    """The marker matters most when the tool fails; it must still be written."""
    job = run(store, tmp_path, ["sh", "-c", "exit 3"])
    done = settle(store, job.id)
    assert done.status == FAILED
    assert done.exit_code == 3


def test_output_lands_in_the_log(store, tmp_path):
    job = run(store, tmp_path, ["sh", "-c", "echo out; echo err >&2"])
    settle(store, job.id)
    text = Path(job.log_path).read_text()
    assert "out" in text and "err" in text


def test_cancel_stops_a_running_job(store, tmp_path):
    job = run(store, tmp_path, ["sleep", "60"])
    store.cancel(job.id)
    done = settle(store, job.id)
    assert done.status == CANCELLED
    assert done.finished_at is not None


def test_cancelling_a_finished_job_is_harmless(store, tmp_path):
    job = run(store, tmp_path, ["/bin/true"])
    settle(store, job.id)
    assert store.cancel(job.id).status == DONE


def test_state_survives_a_restart(tmp_path):
    """Killing and reopening the store must not lose a running job."""
    database = tmp_path / "jobs.sqlite"
    first = JobStore(database)
    job = run(first, tmp_path, ["sleep", "60"])
    first.close()

    second = JobStore(database)
    assert second.get(job.id).status == RUNNING

    second.cancel(job.id)
    assert settle(second, job.id).status == CANCELLED
    second.close()


def test_a_job_that_finished_while_the_server_was_down_is_reconciled(tmp_path):
    """The child writes the marker, so nobody has to be watching."""
    database = tmp_path / "jobs.sqlite"
    first = JobStore(database)
    job = run(first, tmp_path, ["sh", "-c", "exit 2"])
    first.close()

    deadline = time.time() + TIMEOUT
    while time.time() < deadline and not Path(job.marker_path).exists():
        time.sleep(0.05)

    second = JobStore(database)
    seen = second.get(job.id)
    assert seen.status == FAILED
    assert seen.exit_code == 2
    second.close()


def test_a_vanished_process_without_a_marker_is_lost(store, tmp_path):
    """A tool killed with SIGKILL never gets to record anything."""
    job = run(store, tmp_path, ["sleep", "60"])
    os.killpg(os.getpgid(job.pid), signal.SIGKILL)
    assert settle(store, job.id).status == LOST


def test_a_recycled_process_id_does_not_look_alive(store, tmp_path):
    """Process ids are reused, so the recorded start time has to match."""
    job = run(store, tmp_path, ["sleep", "60"])
    store._db.execute("UPDATE jobs SET pid_start = pid_start + 1 WHERE id = ?", (job.id,))
    assert store.get(job.id).status == LOST

    # The store has been told this process is somebody else's, so it will not
    # signal it and neither will the fixture: the test broke that view, so the
    # test cleans up after it.
    os.killpg(os.getpgid(job.pid), signal.SIGKILL)


def test_unknown_job_is_an_error(store):
    with pytest.raises(JobError, match="no such job"):
        store.get("nope")


def test_jobs_are_listed_newest_first(store, tmp_path):
    a = run(store, tmp_path, ["/bin/true"], name="a")
    time.sleep(0.01)
    b = run(store, tmp_path, ["/bin/true"], name="b")
    assert [j.id for j in store.list()][:2] == [b.id, a.id]


def test_missing_tool_is_reported(store, tmp_path):
    """The shell reports 127; the job is failed, not lost."""
    job = run(store, tmp_path, ["/nonexistent/tool"])
    done = settle(store, job.id)
    assert done.status == FAILED
    assert done.exit_code == 127
