# SPDX-License-Identifier: GPL-2.0-only
"""Path validation against the configured workspace root.

Every path that originates from an MCP client passes through resolve() before
it reaches the filesystem, a Quartus command line, or a container mount. No
other module builds a path out of client input.
"""

from pathlib import Path, PurePosixPath

#: Where the workspace is mounted inside weft-tools.
CONTAINER_ROOT = PurePosixPath("/work")


class SandboxError(ValueError):
    """A path escaped the workspace root, or the root itself is unusable."""


def resolve(root: Path, path: str | Path) -> Path:
    """resolve - map a client-supplied path into the workspace

    @root: workspace root; must exist and be a directory
    @path: client-supplied path, either absolute or relative to @root

    Symlinks and ``..`` are resolved first and the result is checked
    afterwards, so a symlink pointing out of the workspace is rejected even
    though its own location is inside. The path itself need not exist, which
    lets callers validate a file they are about to create.

    Return: absolute, symlink-free path guaranteed to lie inside @root.

    Raises SandboxError if @root is missing or not a directory, or if the
    resolved path falls outside it.
    """
    base = _root(root)

    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = base / candidate

    resolved = candidate.resolve()
    if resolved != base and not resolved.is_relative_to(base):
        raise SandboxError(f"path escapes the workspace: {path}")

    return resolved


def container_path(root: Path, path: str | Path) -> PurePosixPath:
    """container_path - express a workspace path as weft-tools sees it

    @root: workspace root
    @path: client-supplied path, validated as in resolve()

    Return: the path under CONTAINER_ROOT that names the same file once the
    workspace is bind-mounted into the container.

    Raises SandboxError under exactly the conditions resolve() does.
    """
    base = _root(root)
    resolved = resolve(base, path)
    return CONTAINER_ROOT / resolved.relative_to(base)


def _root(root: Path) -> Path:
    """_root - resolve the workspace root itself

    The root may legitimately be a symlink, so it is resolved before any
    comparison; otherwise every path under it would look like an escape.
    """
    base = Path(root).resolve()
    if not base.is_dir():
        raise SandboxError(f"workspace root is not a directory: {root}")
    return base
