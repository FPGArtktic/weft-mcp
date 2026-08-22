# SPDX-License-Identifier: GPL-2.0-only
"""Linting HDL sources inside weft-tools.

Verilator handles Verilog and SystemVerilog, GHDL handles VHDL. Neither reads
the other's language, so a mixed-language design is linted one language at a
time; that is a property of the tools, not a limitation worth hiding.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from .. import podman
from ..sandbox import CONTAINER_ROOT, container_path

VERILOG = "verilog"
VHDL = "vhdl"

#: Accepted spellings of the two language groups.
LANGUAGES = {
    "verilog": VERILOG,
    "systemverilog": VERILOG,
    "sv": VERILOG,
    "vhdl": VHDL,
}

#: GHDL keeps its analysis library here; the workspace stays clean.
GHDL_WORKDIR = "/tmp"

#: %Error-WIDTHTRUNC: file:line:col: message   - code and location both optional
_VERILATOR = re.compile(
    r"^%(?P<severity>Error|Warning)(?:-(?P<code>[A-Z0-9_]+))?: "
    r"(?:(?P<file>[^:\s]+):(?P<line>\d+):(?P<column>\d+): )?"
    r"(?P<message>.*)$"
)

#: file:line:col:severity: message [-Wcode]   - note the missing space
_GHDL = re.compile(
    r"^(?P<file>[^:\s]+):(?P<line>\d+):(?P<column>\d+):"
    r"(?P<severity>error|warning|note): "
    r"(?P<message>.*?)(?: \[(?P<code>-W[a-z0-9-]+)\])?$"
)


class LintError(ValueError):
    """The request cannot be linted: unknown language, or no files given."""


@dataclass(frozen=True)
class Diagnostic:
    """One message from a linter.

    @file: path relative to the workspace root, or None for a tool-level message
    @line: 1-based line number, or None
    @column: 1-based column, or None
    @severity: "error", "warning", or "note"
    @message: the text, without the location prefix
    @code: the tool's identifier for the check, e.g. "WIDTHTRUNC" or "-Wunused"
    """

    file: str | None
    line: int | None
    column: int | None
    severity: str
    message: str
    code: str | None


def lint(
    image: str,
    workspace: Path,
    files: list[str],
    language: str,
    timeout: float | None = None,
) -> list[Diagnostic]:
    """lint - check HDL sources without simulating them

    @image: weft-tools image name
    @workspace: sandbox root; every entry of @files resolves inside it
    @files: sources to lint, in dependency order for VHDL
    @language: "verilog" (covers SystemVerilog) or "vhdl"
    @timeout: wall-clock limit in seconds

    Return: diagnostics in the order the tool reported them. An empty list
    means the sources are clean, not that the tool failed to run.

    Raises LintError for an unknown language or an empty file list,
    SandboxError if a path escapes the workspace, and PodmanError if the
    container could not start.
    """
    if not files:
        raise LintError("no files to lint")

    kind = LANGUAGES.get(language.lower())
    if kind is None:
        raise LintError(f"unknown language: {language}; expected one of {sorted(LANGUAGES)}")

    # Paths go in relative to /work, so the tools quote them back the same way
    # and the diagnostics are already workspace-relative.
    relative = [str(container_path(workspace, f).relative_to(CONTAINER_ROOT)) for f in files]

    argv = _verilator(relative) if kind == VERILOG else _ghdl(relative)
    result = podman.run(image, workspace, argv, timeout=timeout)

    pattern = _VERILATOR if kind == VERILOG else _GHDL
    return _parse(result.output, pattern)


def _verilator(files: list[str]) -> list[str]:
    """_verilator - lint-only invocation, with the sources' directories on the
    include path so cross-file references resolve."""
    includes = sorted({str(Path(f).parent) for f in files})
    return ["verilator", "--lint-only", *(f"-I{d}" for d in includes), *files]


def _ghdl(files: list[str]) -> list[str]:
    """_ghdl - analysis-only invocation.

    -Wall is not optional in practice: without it GHDL reports syntax errors
    and nothing else, which makes for a poor linter.
    """
    return ["ghdl", "-a", "--std=08", "-Wall", f"--workdir={GHDL_WORKDIR}", *files]


def _parse(output: str, pattern: re.Pattern) -> list[Diagnostic]:
    """_parse - turn tool output into diagnostics, dropping context lines.

    Lines that do not match are indented source echoes and carets, which carry
    no information the structured result does not already have.
    """
    found = []
    for line in output.splitlines():
        m = pattern.match(line)
        if m is None:
            continue

        message = m["message"].strip()
        if message.startswith("Exiting due to"):
            # Verilator's tally of the diagnostics it just printed.
            continue

        found.append(
            Diagnostic(
                file=m["file"],
                line=int(m["line"]) if m["line"] else None,
                column=int(m["column"]) if m["column"] else None,
                severity=m["severity"].lower(),
                message=message,
                code=m["code"],
            )
        )
    return found
