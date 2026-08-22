# SPDX-License-Identifier: GPL-3.0-only
"""Creating Quartus projects and editing their assignments.

Everything goes through `quartus_sh -t` driving the ::quartus::project Tcl API
rather than writing .qsf text by hand. The API applies Quartus's own quoting
and defaults, and it fails loudly on an assignment name the tool does not know,
which hand-written text does not.

Project names are always passed relative to the working directory. Handing
project_new an absolute path moves the Tcl interpreter's working directory to
the project directory, and it stays moved after project_close.
"""

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .install import Install

#: The four assignment forms .qsf actually uses.
GLOBAL = "global"
LOCATION = "location"
INSTANCE = "instance"
PARAMETER = "parameter"

KINDS = (GLOBAL, LOCATION, INSTANCE, PARAMETER)

#: Assignment names are Quartus identifiers, never arbitrary text.
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")

#: Values needing no Tcl quoting at all.
_BARE = re.compile(r"^[A-Za-z0-9_./:+@=-]+$")

#: Characters that make a braced Tcl word unsafe or unbalanced.
_UNQUOTABLE = set("{}\\\n\r")

TCL_TIMEOUT = 300.0


class ProjectError(RuntimeError):
    """The project cannot be created, opened, or assigned to."""


@dataclass(frozen=True)
class Assignment:
    """One line of a .qsf.

    @kind: "global", "location", "instance" or "parameter"
    @name: assignment name, e.g. "DEVICE"; unused for a location, whose name
           is carried in @value as the pin
    @value: the value, or the pin for a location
    @to: the target entity, instance or port; required except for a global
    """

    kind: str
    value: str
    name: str = ""
    to: str = ""

    def render(self) -> str:
        """render - the Tcl command that applies this assignment

        Return: one line of Tcl.

        Raises ProjectError if the kind is unknown, a required field is
        missing, or a field cannot be safely quoted.
        """
        if self.kind not in KINDS:
            raise ProjectError(f"unknown assignment kind: {self.kind}; expected one of {KINDS}")

        if self.kind == LOCATION:
            return f"set_location_assignment {_word(self.value)} -to {_target(self.to)}"

        name = _name(self.name)
        if self.kind == GLOBAL:
            return f"set_global_assignment -name {name} {_word(self.value)}"

        command = "set_instance_assignment" if self.kind == INSTANCE else "set_parameter"
        return f"{command} -name {name} {_word(self.value)} -to {_target(self.to)}"


def create_project(
    install: Install,
    directory: Path,
    name: str,
    family: str,
    part: str,
    top: str | None = None,
    overwrite: bool = False,
) -> Path:
    """create_project - write a fresh .qpf and .qsf

    @install: the Quartus installation to drive
    @directory: where the project files go; created if absent
    @name: project and revision name, also the file basename
    @family: device family, e.g. "MAX 10"
    @part: device part number, e.g. "10M04SAE144A7G"
    @top: top-level entity; defaults to @name
    @overwrite: replace an existing project of the same name

    Return: the path of the .qpf.

    Raises ProjectError if the project already exists and @overwrite is false,
    or if Quartus rejects the family or part.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    revision = _name(name)

    creation = f"project_new {revision} -revision {revision}"
    if overwrite:
        creation += " -overwrite"

    body = [
        creation,
        Assignment(GLOBAL, name="FAMILY", value=family).render(),
        Assignment(GLOBAL, name="DEVICE", value=part).render(),
        Assignment(GLOBAL, name="TOP_LEVEL_ENTITY", value=top or revision).render(),
    ]
    _run(install, directory, body)

    return directory / f"{revision}.qpf"


def set_assignments(
    install: Install,
    directory: Path,
    name: str,
    assignments: list[Assignment],
) -> Path:
    """set_assignments - apply assignments to an existing project

    @install: the Quartus installation to drive
    @directory: the project directory
    @name: project and revision name
    @assignments: what to set; applied in order, so a later one wins

    Return: the path of the rewritten .qsf.

    Raises ProjectError if the project does not exist, an assignment cannot be
    rendered, or Quartus rejects one.
    """
    revision = _name(name)
    if not (Path(directory) / f"{revision}.qpf").is_file():
        raise ProjectError(f"no project {revision} in {directory}")
    if not assignments:
        raise ProjectError("no assignments given")

    body = [f"project_open {revision} -revision {revision} -force"]
    body += [a.render() for a in assignments]
    _run(install, directory, body)

    return Path(directory) / f"{revision}.qsf"


#: Which .qsf assignment carries which kind of source.
SOURCE_ASSIGNMENTS = {
    "SYSTEMVERILOG_FILE": "systemverilog",
    "VERILOG_FILE": "verilog",
    "VHDL_FILE": "vhdl",
    "SDC_FILE": "sdc",
    "QIP_FILE": "qip",
    "QSYS_FILE": "qsys",
    "MIF_FILE": "mif",
    "HEX_FILE": "hex",
}

#: Single-valued assignments worth reporting about a project.
INFO_ASSIGNMENTS = {
    "FAMILY": "family",
    "DEVICE": "device",
    "TOP_LEVEL_ENTITY": "top_entity",
    "PROJECT_OUTPUT_DIRECTORY": "output_directory",
    "ORIGINAL_QUARTUS_VERSION": "created_with",
    "LAST_QUARTUS_VERSION": "last_opened_with",
}

_GLOBAL = re.compile(
    r'^\s*set_global_assignment\s+-name\s+(?P<name>[A-Z0-9_]+)\s+(?P<value>"[^"]*"|\S+)',
    re.M,
)


def project_info(directory: Path, revision: str) -> dict:
    """project_info - what a project says about itself

    @directory: the project directory
    @revision: revision name, which is the .qsf basename

    Read from the .qsf rather than through Tcl, so a project can be inspected
    on a machine with no Quartus, and without the second or so a Tcl
    invocation costs.

    Return: the device and top entity, the sources grouped by language, and
    the pin and instance assignment counts.

    Raises ProjectError if the .qsf cannot be read.
    """
    revision = _name(revision)
    qsf = Path(directory) / f"{revision}.qsf"
    try:
        text = qsf.read_text(errors="replace")
    except OSError as e:
        raise ProjectError(f"cannot read {qsf.name}: {e}") from e

    info: dict = {"revision": revision, "sources": {}}
    for m in _GLOBAL.finditer(text):
        name, value = m["name"], m["value"].strip('"')
        if name in INFO_ASSIGNMENTS:
            info.setdefault(INFO_ASSIGNMENTS[name], value)
        elif name in SOURCE_ASSIGNMENTS:
            info["sources"].setdefault(SOURCE_ASSIGNMENTS[name], []).append(value)

    info["pin_assignments"] = len(re.findall(r"^\s*set_location_assignment\s", text, re.M))
    info["instance_assignments"] = len(re.findall(r"^\s*set_instance_assignment\s", text, re.M))
    info["parameters"] = len(re.findall(r"^\s*set_parameter\s", text, re.M))
    return info


def list_projects(root: Path) -> list[str]:
    """list_projects - project files anywhere under @root

    Reads the filesystem rather than Quartus: a .qpf is a project whether or
    not any tool has ever opened it.

    Return: paths relative to @root, sorted.
    """
    root = Path(root)
    return sorted(str(p.relative_to(root)) for p in root.rglob("*.qpf"))


def _run(install: Install, directory: Path, body: list[str]) -> str:
    """_run - execute a Tcl script against the project in @directory

    The script goes to a temporary file outside the project, because
    --tcl_eval takes a single command and its arguments rather than a script,
    and a script file dropped in the project directory would sit there looking
    like a source. quartus_sh runs with @directory as its working directory,
    which is what lets project names stay relative.

    Return: the combined output.

    Raises ProjectError if quartus_sh cannot run or reports an error.
    """
    script = "\n".join(
        ["package require ::quartus::project", *body, "export_assignments", "project_close", ""]
    )

    environment = dict(os.environ)
    environment.update(install.env)

    with tempfile.TemporaryDirectory(prefix="weft-tcl-") as scratch:
        path = Path(scratch) / "project.tcl"
        path.write_text(script)
        return _quartus(install, directory, path, environment)


def _quartus(install: Install, directory: Path, script: Path, environment: dict[str, str]) -> str:
    """_quartus - run one Tcl script and turn a Quartus failure into an error."""
    try:
        done = subprocess.run(
            [str(install.require("quartus_sh")), "-t", str(script)],
            cwd=str(directory),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=TCL_TIMEOUT,
            env=environment,
            check=False,
        )
    except OSError as e:
        raise ProjectError(f"cannot run quartus_sh: {e}") from e
    except subprocess.TimeoutExpired as e:
        raise ProjectError(f"quartus_sh did not finish within {TCL_TIMEOUT}s") from e

    if done.returncode != 0 or _errors(done.stdout):
        raise ProjectError(_errors(done.stdout) or f"quartus_sh failed ({done.returncode})")
    return done.stdout


def _errors(output: str) -> str:
    """_errors - the Quartus error lines in @output, joined.

    Quartus prefixes them "Error" or "Error (12345)"; anything else is Info or
    Warning and is none of this function's business.
    """
    found = [ln.strip() for ln in output.splitlines() if ln.lstrip().startswith("Error")]
    return "; ".join(found)


def _name(value: str) -> str:
    """_name - a Quartus identifier, rejected outright if it is not one."""
    if not _NAME.match(value):
        raise ProjectError(f"not a usable Quartus name: {value!r}")
    return value


def _target(value: str) -> str:
    """_target - the -to operand, which may be a port with a bit index."""
    if not value:
        raise ProjectError("this assignment needs a target")
    return _word(value)


def _word(value: str) -> str:
    """_word - one Tcl word, quoted so nothing in it is ever substituted

    Braces stop every kind of substitution, which is what a value out of an
    MCP client needs. A value carrying braces or a backslash cannot be brace
    quoted safely and is refused rather than escaped: nothing Quartus accepts
    needs them, so the only thing an escape would enable is an injection.
    """
    if not value:
        raise ProjectError("empty value")
    if _BARE.match(value):
        return value
    if _UNQUOTABLE & set(value):
        raise ProjectError(f"value cannot be quoted for Tcl: {value!r}")
    return "{" + value + "}"
