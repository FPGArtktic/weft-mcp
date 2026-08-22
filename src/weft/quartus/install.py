# SPDX-License-Identifier: GPL-2.0-only
"""Locating and identifying a host Quartus installation.

Everything that runs a Quartus tool goes through an Install: it knows where
the binaries are, which edition they belong to, and what environment they need.
"""

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

LITE = "lite"
STANDARD = "standard"
PRO = "pro"

#: How each edition names itself in the version banner. Lite calls itself
#: "SC Lite Edition"; matching on the word alone covers every spelling seen.
EDITION_WORDS = {"Lite": LITE, "Standard": STANDARD, "Pro": PRO}

#: Version banner, e.g. "Version 25.1std.0 Build 1129 10/21/2025 SC Lite Edition"
_BANNER = re.compile(
    r"^Version\s+(?P<version>\S+)\s+Build\s+(?P<build>\S+)\s+\S+\s+(?P<edition>.*?)\s*$",
    re.M,
)

PROBE_TIMEOUT = 60.0


class InstallError(RuntimeError):
    """The installation is missing, unusable, or not the edition claimed."""


@dataclass(frozen=True)
class Install:
    """One Quartus installation on the host.

    @root: install root, the directory holding bin/quartus_sh
    @edition: "lite", "standard" or "pro"
    @version: version string as the tools report it, e.g. "25.1std.0"
    @build: build number from the same banner
    @env: extra environment for every invocation, carrying FlexLM variables
          through to the Pro edition
    """

    root: Path
    edition: str
    version: str
    build: str
    env: dict[str, str] = field(default_factory=dict)

    def tool(self, name: str) -> Path:
        """tool - absolute path to one Quartus executable

        @name: executable name, for instance "quartus_map"

        The wrapper scripts work out the install root from their own path, so
        they must be invoked through a real path inside <root>/bin. A symlink
        placed elsewhere breaks them, and setting QUARTUS_ROOTDIR in the
        environment does nothing: the wrapper ignores it and honours only
        QUARTUS_ROOTDIR_OVERRIDE.

        Return: the path, whether or not it exists.
        """
        return self.root / "bin" / name

    def require(self, name: str) -> Path:
        """require - the path to a tool that has to be there

        Raises InstallError when the executable is missing.
        """
        path = self.tool(name)
        if not path.is_file():
            raise InstallError(f"{name} is missing from {self.root}")
        return path

    @property
    def synthesis_tool(self) -> str:
        """synthesis_tool - the synthesis executable for this edition

        Pro replaced quartus_map with quartus_syn. Both files exist in a Lite
        installation, so the choice cannot be made by looking for one: on Lite
        quartus_syn is a stub that refuses to run.
        """
        return "quartus_syn" if self.edition == PRO else "quartus_map"


def probe(root: Path, env: dict[str, str] | None = None) -> Install:
    """probe - identify the installation at @root

    @root: install root, the directory holding bin/quartus_sh
    @env: extra environment, passed to the tool and carried on the result

    The edition comes from the version banner rather than from which files are
    present, because a Lite installation ships the Pro-only binaries as stubs.

    Return: the Install.

    Raises InstallError if quartus_sh is missing, fails to run, or prints a
    banner this cannot read.
    """
    root = Path(root)
    executable = root / "bin" / "quartus_sh"
    if not executable.is_file():
        raise InstallError(f"no bin/quartus_sh under {root}")

    try:
        done = subprocess.run(
            [str(executable), "--version"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=PROBE_TIMEOUT,
            env=_environment(env),
            check=False,
        )
    except OSError as e:
        raise InstallError(f"cannot run {executable}: {e}") from e
    except subprocess.TimeoutExpired as e:
        raise InstallError(f"{executable} did not answer within {PROBE_TIMEOUT}s") from e

    if done.returncode != 0:
        raise InstallError(
            f"{executable} --version failed ({done.returncode}): {done.stderr.strip()}"
        )

    return _parse(root, done.stdout, dict(env or {}))


def _parse(root: Path, banner: str, env: dict[str, str]) -> Install:
    """_parse - read version, build and edition out of the version banner."""
    m = _BANNER.search(banner)
    if m is None:
        raise InstallError(f"cannot read the version banner from {root}: {banner.strip()[:120]!r}")

    words = m["edition"]
    edition = next((e for word, e in EDITION_WORDS.items() if word in words), None)
    if edition is None:
        raise InstallError(f"unknown Quartus edition: {words!r}")

    return Install(
        root=root,
        edition=edition,
        version=m["version"],
        build=m["build"],
        env=env,
    )


def _environment(env: dict[str, str] | None) -> dict[str, str]:
    """_environment - the environment a Quartus tool runs under.

    QUARTUS_ROOTDIR is deliberately not set: the wrapper ignores it. Anything
    the configuration supplies, FlexLM variables in particular, is passed on
    untouched.
    """
    import os

    environment = dict(os.environ)
    if env:
        environment.update(env)
    return environment
