# SPDX-License-Identifier: GPL-3.0-only
"""Linting HDL sources.

Verilator handles Verilog and SystemVerilog, GHDL handles VHDL. Neither reads
the other's language, so a mixed-language design is linted one language at a
time and the sources left out are named in the result; that is a property of
the tools, not a limitation worth hiding.

Questa is the exception, as it is for simulation: it reads both in one pass,
so a VHDL entity instantiated from SystemVerilog is actually resolved rather
than reported missing. What it gives is a compile check with the vendor's own
front-end, not a style linter -- full lint is a licensed feature the Starter
Edition does not carry, and claiming otherwise would oversell a clean result.
"""

import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .. import podman
from ..sandbox import CONTAINER_ROOT, container_path
from . import questa

VERILOG = "verilog"
VHDL = "vhdl"

VERILATOR = "verilator"
GHDL = "ghdl"
QUESTA = "questa"

#: The linter each language gets when the caller names none.
DEFAULT_LINTER = {VERILOG: VERILATOR, VHDL: GHDL}

LINTER_LANGUAGE = {VERILATOR: VERILOG, GHDL: VHDL}

#: Linters that read both languages in one pass, and so exclude nothing.
BILINGUAL = {QUESTA}

LINTERS = sorted(LINTER_LANGUAGE | dict.fromkeys(BILINGUAL))

#: Which suffix belongs to which language, as for simulation.
SUFFIX_LANGUAGE = {
    ".v": VERILOG,
    ".sv": VERILOG,
    ".svh": VERILOG,
    ".vh": VERILOG,
    ".vhd": VHDL,
    ".vhdl": VHDL,
}

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

#: ** Error: src/a.sv(4): (vlog-2730) message
#: ** Error: (vlog-13069) src/a.sv(7): message
#: Questa puts the message id before or after the location depending on which
#: pass produced it, so both places are optional and either may carry it.
_QUESTA = re.compile(
    r"^\*\* (?P<severity>Error|Warning|Note)(?: \([^)]*\))?: "
    r"(?:\((?P<code_first>[a-z]+-\d+)\) )?"
    r"(?:(?P<file>[^\s():]+)\((?P<line>\d+)\): )?"
    r"(?:\((?P<code_last>[a-z]+-\d+)\) )?"
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


@dataclass(frozen=True)
class Lint:
    """The outcome of one lint run.

    @linter: which of verilator, ghdl, questa actually ran
    @diagnostics: in the order the tool reported them
    @excluded: sources left out as belonging to the other language; empty for
               a single-language run and for Questa, which reads both
    """

    linter: str
    diagnostics: list[Diagnostic] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)


def lint(
    image: str,
    workspace: Path,
    files: list[str],
    language: str | None = None,
    linter: str | None = None,
    timeout: float | None = None,
    install: "questa.Install | None" = None,
) -> Lint:
    """lint - check HDL sources without simulating them

    @image: weft-tools image name
    @workspace: sandbox root; every entry of @files resolves inside it
    @files: sources to lint, in dependency order for VHDL
    @language: "verilog" (covers SystemVerilog) or "vhdl"; inferred from the
               suffixes when not given
    @linter: "verilator", "ghdl" or "questa"; defaults to the one that fits
             the language. Questa reads both languages in one pass and needs
             a configured installation.
    @timeout: wall-clock limit in seconds
    @install: the probed Questa installation, needed only for that linter

    Sources in the language that is not being checked are left out rather than
    handed to a tool that cannot read them, and they are named in the result.
    Passing a whole mixed project to Verilator otherwise produces a confident
    complaint about a module that is not missing at all, only written in VHDL.

    Return: a Lint carrying the diagnostics. An empty list means the sources
    are clean, not that the tool failed to run.

    Raises LintError for an unknown language or linter, an empty file list, or
    a mixed set with nothing to resolve it by; SandboxError if a path escapes
    the workspace; PodmanError if the container could not start.
    """
    if not files:
        raise LintError("no files to lint")

    tool, kept, excluded = _plan(files, language, linter)

    if tool == QUESTA:
        output = _questa(install, workspace, kept, timeout)
        pattern = _QUESTA
    else:
        # Paths go in relative to /work, so the tools quote them back the same
        # way and the diagnostics are already workspace-relative.
        relative = [str(container_path(workspace, f).relative_to(CONTAINER_ROOT)) for f in kept]
        argv = _verilator(relative) if tool == VERILATOR else _ghdl(relative)
        output = podman.run(image, workspace, argv, timeout=timeout).output
        pattern = _VERILATOR if tool == VERILATOR else _GHDL

    return Lint(linter=tool, diagnostics=_parse(output, pattern), excluded=excluded)


def _plan(
    files: list[str], language: str | None, linter: str | None
) -> tuple[str, list[str], list[str]]:
    """_plan - decide which linter runs over which sources

    @files: everything the caller offered
    @language: the language asked for, or None
    @linter: the linter asked for, or None

    Return: the linter, the sources to hand it, and the sources left out.

    Raises LintError for an unknown language or linter, an unrecognised
    suffix, a linter that reads none of the sources, or a mixed set with
    neither a language nor a linter to resolve it.
    """
    found: dict[str, list[str]] = {}
    for f in files:
        kind = SUFFIX_LANGUAGE.get(Path(f).suffix.lower())
        if kind is None:
            raise LintError(f"unrecognised source type: {f}")
        found.setdefault(kind, []).append(f)

    if linter is not None:
        tool = linter.lower()
        if tool in BILINGUAL:
            return tool, files, []
        if tool not in LINTER_LANGUAGE:
            raise LintError(f"unknown linter: {linter}; expected one of {LINTERS}")
        wanted = LINTER_LANGUAGE[tool]
        if wanted not in found:
            raise LintError(f"{tool} does not read {'/'.join(sorted(found))} sources")
        return tool, found[wanted], _others(found, wanted)

    if language is not None:
        wanted = LANGUAGES.get(language.lower())
        if wanted is None:
            raise LintError(f"unknown language: {language}; expected one of {sorted(LANGUAGES)}")
        if wanted not in found:
            raise LintError(f"no {wanted} sources among the files given")
        return DEFAULT_LINTER[wanted], found[wanted], _others(found, wanted)

    if len(found) == 1:
        kind, kept = next(iter(found.items()))
        return DEFAULT_LINTER[kind], kept, []

    listing = "; ".join(f"{k}: {', '.join(fs)}" for k, fs in sorted(found.items()))
    raise LintError(
        f"source set spans both languages ({listing}). Neither Verilator nor "
        "GHDL reads both; name a language to check one of them, or name questa "
        "to check the design whole"
    )


def _others(found: dict[str, list[str]], language: str) -> list[str]:
    """_others - the sources in the language that is not being checked."""
    return [f for kind, fs in sorted(found.items()) if kind != language for f in fs]


def _questa(
    install: "questa.Install | None",
    workspace: Path,
    files: list[str],
    timeout: float | None,
) -> str:
    """_questa - compile-check on the host, over workspace-relative paths

    The work library goes in a temporary directory, so the check leaves the
    workspace exactly as it found it and no stale library from an earlier
    source tree can be compiled against.

    Raises LintError when no Questa is configured.
    """
    if install is None:
        raise LintError(
            "no Questa configured; add a [questa] section naming its root, or "
            "use one of the containerised linters"
        )

    verilog, vhdl = [], []
    for f in files:
        # Validate against the workspace, then hand the tool the relative form
        # so the diagnostics come back naming what the caller passed in.
        relative = str(container_path(workspace, f).relative_to(CONTAINER_ROOT))
        target = vhdl if SUFFIX_LANGUAGE[Path(f).suffix.lower()] == VHDL else verilog
        target.append(relative)

    with tempfile.TemporaryDirectory(prefix="weft-questa-") as scratch:
        return questa.check(
            install, workspace, Path(scratch), verilog, vhdl, timeout=timeout
        ).output


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

        groups = m.groupdict()
        code = groups.get("code") or groups.get("code_first") or groups.get("code_last")
        if code in questa.NOISE:
            # Questa names its own ini file on every invocation.
            continue

        found.append(
            Diagnostic(
                file=groups.get("file"),
                line=int(groups["line"]) if groups.get("line") else None,
                column=int(groups["column"]) if groups.get("column") else None,
                severity=m["severity"].lower(),
                message=message,
                code=code,
            )
        )
    return found
