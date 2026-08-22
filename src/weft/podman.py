# SPDX-License-Identifier: GPL-2.0-only
"""Running commands inside the weft-tools container.

Every containerised tool goes through run(). It is the single place that pins
--network=none and mounts nothing but the workspace, so no caller can widen
the sandbox by accident.
"""

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .sandbox import CONTAINER_ROOT

#: podman's own exit status when it cannot start the container at all.
PODMAN_FAILURE = 125


class PodmanError(RuntimeError):
    """podman is missing, the image is missing, or the container never ran."""


@dataclass(frozen=True)
class Result:
    """What one container run produced.

    @returncode: exit status of the command inside the container
    @stdout: captured standard output
    @stderr: captured standard error
    """

    returncode: int
    stdout: str
    stderr: str

    @property
    def output(self) -> str:
        """output - both streams joined, for tools that split diagnostics."""
        return self.stdout + self.stderr


def run(
    image: str,
    workspace: Path,
    argv: list[str],
    timeout: float | None = None,
) -> Result:
    """run - execute one command inside weft-tools

    @image: name of the locally built image
    @workspace: host directory bind-mounted at /work; nothing else is visible
    @argv: command and arguments, executed with /work as working directory
    @timeout: wall-clock limit in seconds, or None for no limit

    The container gets no network. Under rootless podman the container's root
    maps to the calling user, so whatever the tool writes into the workspace
    belongs to that user.

    Return: Result carrying the command's exit status and captured output.

    Raises PodmanError if podman is absent or could not start the container,
    and subprocess.TimeoutExpired if @timeout elapses.
    """
    command = [
        "podman",
        "run",
        "--rm",
        "--network=none",
        "-v",
        f"{workspace}:{CONTAINER_ROOT}",
        "-w",
        str(CONTAINER_ROOT),
        image,
        *argv,
    ]

    try:
        done = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as e:
        raise PodmanError("podman is not installed or not on PATH") from e

    if done.returncode == PODMAN_FAILURE:
        raise PodmanError(f"podman could not start {image}: {done.stderr.strip()}")

    return Result(done.returncode, done.stdout, done.stderr)
