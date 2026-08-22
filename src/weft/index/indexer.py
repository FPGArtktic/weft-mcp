# SPDX-License-Identifier: GPL-3.0-only
"""Indexing a directory of HDL on demand.

Nothing watches the filesystem. A caller asks for a directory and gets what is
there at that moment; a file whose content hash has not changed is not parsed
again.
"""

import shlex
from dataclasses import dataclass, field
from pathlib import Path

from .. import podman
from ..sandbox import CONTAINER_ROOT, container_path, resolve
from . import verilog as verilog_extract
from . import vhdl as vhdl_extract
from .model import Module
from .store import SymbolStore, digest

VERILOG_SUFFIXES = (".v", ".sv", ".svh", ".vh")
VHDL_SUFFIXES = (".vhd", ".vhdl")

#: GHDL's scratch library, inside the workspace so one container run can
#: analyse and dump in sequence without carrying state between runs.
GHDL_LIBRARY = Path(".weft") / "ghdl"

#: Separates the per-file dumps in the single GHDL run.
MARKER = "===weft-file==="

DEFAULT_TIMEOUT = 600.0


class IndexError_(RuntimeError):
    """The directory cannot be indexed."""


@dataclass
class Indexed:
    """What one indexing run did.

    @modules: how many modules and entities are now recorded for this run
    @parsed: files read this time
    @skipped: files whose content had not changed
    @removed: indexed files that are no longer on disk
    @failed: files a parser rejected, with its complaint
    """

    modules: int = 0
    parsed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)


def index_project(
    image: str,
    workspace: Path,
    directory: str,
    store: SymbolStore,
    timeout: float | None = DEFAULT_TIMEOUT,
) -> Indexed:
    """index_project - read a directory of HDL into the symbol store

    @image: weft-tools image name
    @workspace: sandbox root
    @directory: workspace-relative directory to index
    @store: where the symbols go
    @timeout: wall-clock limit for each parser run

    Return: an Indexed describing what happened.

    Raises SandboxError if @directory escapes the workspace, IndexError_ if it
    is not a directory, and PodmanError if the container could not start.
    """
    root = resolve(workspace, directory)
    if not root.is_dir():
        raise IndexError_(f"not a directory: {directory}")

    sources = sorted(
        p for p in root.rglob("*") if p.suffix.lower() in VERILOG_SUFFIXES + VHDL_SUFFIXES
    )
    relative = {str(p.relative_to(Path(workspace).resolve())): p for p in sources}

    report = Indexed(removed=store.forget_all_but(set(relative)))

    stale = {}
    for path, absolute in relative.items():
        text = absolute.read_text(errors="replace")
        if store.unchanged(path, digest(text)):
            report.skipped.append(path)
            continue
        stale[path] = text

    verilog = {p: t for p, t in stale.items() if Path(p).suffix.lower() in VERILOG_SUFFIXES}
    vhdl = {p: t for p, t in stale.items() if Path(p).suffix.lower() in VHDL_SUFFIXES}

    found: dict[str, list[Module]] = {}
    if verilog:
        found.update(_verilog(image, workspace, verilog, report, timeout))
    if vhdl:
        found.update(_vhdl(image, workspace, vhdl, report, timeout))

    for path, modules in found.items():
        store.replace(path, digest(stale[path]), modules)
        report.parsed.append(path)
        report.modules += len(modules)

    report.parsed.sort()
    return report


def _verilog(
    image: str, workspace: Path, files: dict[str, str], report: Indexed, timeout: float | None
) -> dict[str, list[Module]]:
    """_verilog - parse every Verilog and SystemVerilog file in one run.

    Verible reads the whole set at once and keys its output by the paths it was
    given, so one container start covers the lot.
    """
    argv = ["verible-verilog-syntax", "--export_json", "--printtree", *files]
    result = podman.run(image, workspace, argv, timeout=timeout)

    start = result.output.find("{")
    if start < 0:
        for path in files:
            report.failed[path] = result.output.strip()[:200] or "verible produced no tree"
        return {}

    modules = verilog_extract.modules(result.output[start:], files)
    by_file: dict[str, list[Module]] = {path: [] for path in files}
    for module in modules:
        by_file.setdefault(module.file, []).append(module)
    return by_file


def _vhdl(
    image: str, workspace: Path, files: dict[str, str], report: Indexed, timeout: float | None
) -> dict[str, list[Module]]:
    """_vhdl - analyse every VHDL file, then dump each one's tree.

    The dump analyses rather than parses, so an entity instantiated directly
    has to be in the library already. Analysis and dumping therefore happen in
    one container run, in that order, with the dumps separated by a marker.
    Order within the analysis is left to GHDL, which reports what it cannot
    resolve rather than guessing.
    """
    library = container_path(workspace, GHDL_LIBRARY)
    resolve(workspace, GHDL_LIBRARY).mkdir(parents=True, exist_ok=True)

    quoted = " ".join(shlex.quote(p) for p in files)
    lines = [
        f"ghdl -a --std=08 --workdir={shlex.quote(str(library))} {quoted} || true",
    ]
    for path in files:
        lines.append(f"echo {shlex.quote(MARKER + ' ' + path)}")
        lines.append(
            f"ghdl --file-to-xml --std=08 --workdir={shlex.quote(str(library))} "
            f"{shlex.quote(path)} 2>&1 || true"
        )

    result = podman.run(image, workspace, ["sh", "-c", "\n".join(lines)], timeout=timeout)

    by_file: dict[str, list[Module]] = {}
    for chunk in result.output.split(MARKER)[1:]:
        head, _, body = chunk.partition("\n")
        path = head.strip()
        start = body.find("<?xml")
        if start < 0:
            report.failed[path] = " ".join(body.split())[:200] or "ghdl produced no tree"
            continue
        by_file[path] = vhdl_extract.modules(body[start:], path, files[path])
    return by_file


def container_root() -> str:
    """container_root - where the workspace is mounted, for callers building paths."""
    return str(CONTAINER_ROOT)
