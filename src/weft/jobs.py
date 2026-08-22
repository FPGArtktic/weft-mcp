# SPDX-License-Identifier: GPL-2.0-only
"""Persistent job store for long-running compilations.

A compilation outlives the request that started it and may outlive the server
itself. Job state therefore lives in SQLite, and every job is launched through
a shell that records the exit status in a marker file once the tool returns.

Quartus writes no such marker of its own: the <revision>.done file it leaves
behind holds a timestamp and nothing else, so it cannot say whether the flow
succeeded. Without a marker, a job whose server died would be unknowable.
"""

import contextlib
import os
import signal
import sqlite3
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

RUNNING = "running"
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"
#: Not in the vocabulary PROJECT.md 4.2 lists. That vocabulary assumes the
#: exit marker always exists, which holds only because WEFT writes it: a
#: SIGKILL leaves nothing behind. Calling that "failed" would claim knowledge
#: of an exit status nobody ever saw.
LOST = "lost"

#: Quartus return codes. 0 and 2-4 are documented; 1 is not, but a SIGTERM'd
#: quartus_sh exits with it normally rather than being reported as signalled.
EXIT_STATUS = {
    0: DONE,
    1: CANCELLED,
    2: FAILED,
    3: FAILED,
    4: CANCELLED,
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    project     TEXT NOT NULL,
    revision    TEXT NOT NULL,
    flow        TEXT NOT NULL,
    log_path    TEXT NOT NULL,
    marker_path TEXT NOT NULL,
    pid         INTEGER,
    pid_start   INTEGER,
    status      TEXT NOT NULL,
    exit_code   INTEGER,
    created_at  REAL NOT NULL,
    finished_at REAL,
    cancelled_at REAL
);
"""


class JobError(RuntimeError):
    """The job cannot be started, found, or acted on."""


@dataclass(frozen=True)
class Job:
    """One compilation, running or finished.

    @id: opaque identifier handed back to the client
    @project: project path the flow was started against
    @revision: Quartus revision name
    @flow: flow name, for instance "compile"
    @log_path: file collecting the tool's output
    @status: running, done, failed, cancelled or lost
    @exit_code: the tool's exit status, once it has one
    @pid: process id while running
    @created_at / @finished_at: seconds since the epoch
    @cancelled_at: when a stop was asked for, if it ever was
    """

    id: str
    project: str
    revision: str
    flow: str
    log_path: str
    marker_path: str
    pid: int | None
    pid_start: int | None
    status: str
    exit_code: int | None
    created_at: float
    finished_at: float | None
    cancelled_at: float | None


class JobStore:
    """SQLite-backed job table.

    Every read reconciles first, so a caller never sees a job reported as
    running when its process is gone.

    Connections are per thread. A SQLite connection may only be used by the
    thread that opened it, and the MCP server runs synchronous tools in a
    worker thread, so a single shared connection made at startup would be
    unusable by every tool call. Write-ahead logging lets the connections
    share the file without blocking each other.
    """

    def __init__(self, database: Path):
        """__init__ - open, and create the schema if this is a fresh database.

        @database: SQLite file; parent directories are created as needed
        """
        self._database = Path(database)
        self._database.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._db.executescript(SCHEMA)

    @property
    def _db(self) -> sqlite3.Connection:
        """_db - this thread's connection, opened on first use."""
        connection = getattr(self._local, "connection", None)
        if connection is None:
            connection = sqlite3.connect(self._database, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            self._local.connection = connection
        return connection

    def close(self) -> None:
        """close - release this thread's handle.

        Connections opened by other threads are left to be collected when
        those threads end; SQLite will not let one thread close another's.
        """
        connection = getattr(self._local, "connection", None)
        if connection is not None:
            connection.close()
            self._local.connection = None

    def start(
        self,
        argv: list[str],
        cwd: Path,
        project: str,
        revision: str,
        flow: str,
        log_path: Path,
        env: dict[str, str] | None = None,
    ) -> Job:
        """start - launch a tool and record the job

        @argv: command to run
        @cwd: working directory for the tool
        @project: project path, for reporting
        @revision: Quartus revision name
        @flow: flow name
        @log_path: where the tool's output is collected
        @env: extra environment on top of the server's own

        The command runs under a shell that appends its exit status to a
        marker file. The marker is what makes a finished job readable after a
        restart, so it is written by the child rather than by this process.

        Return: the Job, already in the running state.

        Raises JobError if the tool could not be launched.
        """
        job_id = uuid.uuid4().hex
        marker = Path(f"{log_path}.exit")
        marker.unlink(missing_ok=True)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        # No "set -e": the marker matters most when the tool fails, and an
        # errexit shell would abort before writing it.
        script = '"$@" ; echo $? > "$WEFT_MARKER"\n'
        environment = dict(os.environ)
        if env:
            environment.update(env)
        environment["WEFT_MARKER"] = str(marker)

        try:
            with open(log_path, "wb") as log:
                child = subprocess.Popen(
                    ["sh", "-c", script, "sh", *argv],
                    cwd=str(cwd),
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    env=environment,
                    start_new_session=True,
                )
        except OSError as e:
            raise JobError(f"cannot start {argv[0]}: {e}") from e

        self._db.execute(
            "INSERT INTO jobs (id, project, revision, flow, log_path, marker_path,"
            " pid, pid_start, status, exit_code, created_at, finished_at,"
            " cancelled_at) VALUES (?,?,?,?,?,?,?,?,?,NULL,?,NULL,NULL)",
            (
                job_id,
                project,
                revision,
                flow,
                str(log_path),
                str(marker),
                child.pid,
                _start_ticks(child.pid),
                RUNNING,
                time.time(),
            ),
        )
        return self.get(job_id)

    def get(self, job_id: str) -> Job:
        """get - one job, reconciled

        @job_id: identifier returned by start()

        Return: the Job.

        Raises JobError if no such job exists.
        """
        row = self._db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise JobError(f"no such job: {job_id}")
        return self._reconcile(row)

    def list(self, limit: int = 50) -> list[Job]:
        """list - recent jobs, newest first, all reconciled."""
        rows = self._db.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._reconcile(r) for r in rows]

    def cancel(self, job_id: str) -> Job:
        """cancel - ask a running job to stop

        @job_id: identifier returned by start()

        The whole process group is signalled: quartus_sh is a shell wrapper
        around the real tool, so signalling the leader alone would leave the
        tool running.

        That same signal also kills the shell that would have written the exit
        marker, so the request is recorded in the database first. A job that
        then disappears without a marker is known to have been cancelled
        rather than merely lost, and the distinction survives a restart.

        Return: the Job as it stands after signalling.

        Raises JobError if the job does not exist.
        """
        job = self.get(job_id)
        if job.status != RUNNING or job.pid is None:
            return job

        self._db.execute(
            "UPDATE jobs SET cancelled_at = ? WHERE id = ? AND cancelled_at IS NULL",
            (time.time(), job_id),
        )
        # It may have finished between the reconcile and the signal.
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(os.getpgid(job.pid), signal.SIGTERM)
        return self.get(job_id)

    def _reconcile(self, row: sqlite3.Row) -> Job:
        """_reconcile - work out what really happened to a job

        A job recorded as running is only still running if its process is
        alive and is the same process: process ids are reused, so the recorded
        start time has to match too. Otherwise the marker decides, and a
        missing marker means the tool died without ever returning.
        """
        if row["status"] != RUNNING:
            return _job(row)

        code = _read_marker(Path(row["marker_path"]))
        if code is not None:
            return self._finish(row["id"], EXIT_STATUS.get(code, FAILED), code)

        if _alive(row["pid"], row["pid_start"]):
            return _job(row)

        # Gone without a word. If a stop was asked for, that is why.
        died = CANCELLED if row["cancelled_at"] else LOST
        return self._finish(row["id"], died, None)

    def _finish(self, job_id: str, status: str, code: int | None) -> Job:
        """_finish - record a terminal status once, and hand back the job."""
        self._db.execute(
            "UPDATE jobs SET status = ?, exit_code = ?, finished_at = ?,"
            " pid = NULL, pid_start = NULL WHERE id = ?",
            (status, code, time.time(), job_id),
        )
        row = self._db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _job(row)


def _job(row: sqlite3.Row) -> Job:
    """_job - turn a database row into a Job."""
    return Job(
        id=row["id"],
        project=row["project"],
        revision=row["revision"],
        flow=row["flow"],
        log_path=row["log_path"],
        marker_path=row["marker_path"],
        pid=row["pid"],
        pid_start=row["pid_start"],
        status=row["status"],
        exit_code=row["exit_code"],
        created_at=row["created_at"],
        finished_at=row["finished_at"],
        cancelled_at=row["cancelled_at"],
    )


def _read_marker(marker: Path) -> int | None:
    """_read_marker - the exit status the child recorded, if it got that far.

    A marker that exists but holds nothing means the child was interrupted
    between finishing and writing, which is indistinguishable from not having
    finished at all.
    """
    try:
        text = marker.read_text().strip()
    except OSError:
        return None
    if not text.isdigit():
        return None
    return int(text)


def _start_ticks(pid: int) -> int | None:
    """_start_ticks - when a process started, in clock ticks since boot

    Recorded alongside the process id so that a recycled id cannot be mistaken
    for the original process after a restart. Returns None where /proc is not
    available, which only costs the extra certainty.
    """
    fields = _stat(pid)
    return None if fields is None else int(fields[19])


def _stat(pid: int) -> list[str] | None:
    """_stat - /proc/<pid>/stat from the state field onwards

    The command name can contain spaces and brackets, so the split happens
    after its closing parenthesis. Field 3 of the file is then element 0.
    """
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    return stat.rsplit(")", 1)[1].split()


def _alive(pid: int | None, start: int | None) -> bool:
    """_alive - whether the recorded process is still the one running."""
    if pid is None:
        return False

    fields = _stat(pid)
    if fields is None:
        return False
    if fields[0] == "Z":
        # A dead child stays visible until it is reaped, and kill(pid, 0)
        # succeeds on it, so the process id alone proves nothing.
        return False
    if start is None:
        return True
    return int(fields[19]) == start
