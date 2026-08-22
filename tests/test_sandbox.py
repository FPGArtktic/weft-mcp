# SPDX-License-Identifier: GPL-3.0-only
"""Tests for workspace path validation."""

from pathlib import PurePosixPath

import pytest

from weft.sandbox import SandboxError, container_path, resolve


def test_relative_path_stays_inside(tmp_path):
    (tmp_path / "src").mkdir()
    assert resolve(tmp_path, "src/top.sv") == tmp_path / "src" / "top.sv"


def test_absolute_path_inside_is_accepted(tmp_path):
    target = tmp_path / "top.sv"
    assert resolve(tmp_path, target) == target


def test_path_need_not_exist(tmp_path):
    """Callers validate output files before creating them."""
    assert resolve(tmp_path, "output_files/top.sof").name == "top.sof"


def test_root_itself_resolves(tmp_path):
    assert resolve(tmp_path, ".") == tmp_path


def test_dotdot_escape_is_rejected(tmp_path):
    (tmp_path / "src").mkdir()
    with pytest.raises(SandboxError):
        resolve(tmp_path, "src/../../etc/passwd")


def test_absolute_path_outside_is_rejected(tmp_path):
    with pytest.raises(SandboxError):
        resolve(tmp_path, "/etc/passwd")


def test_symlink_out_of_workspace_is_rejected(tmp_path):
    """The link sits inside the workspace; its target does not."""
    outside = tmp_path.parent / "outside"
    outside.mkdir()
    (tmp_path / "escape").symlink_to(outside)
    with pytest.raises(SandboxError):
        resolve(tmp_path, "escape/secret.txt")


def test_symlinked_root_is_not_an_escape(tmp_path):
    """A workspace reached through a symlink must still accept its own files."""
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    assert resolve(link, "top.sv") == real / "top.sv"


def test_missing_root_is_rejected(tmp_path):
    with pytest.raises(SandboxError):
        resolve(tmp_path / "nope", "top.sv")


def test_file_as_root_is_rejected(tmp_path):
    root = tmp_path / "file"
    root.write_text("")
    with pytest.raises(SandboxError):
        resolve(root, "top.sv")


def test_container_path_maps_under_work(tmp_path):
    (tmp_path / "src").mkdir()
    got = container_path(tmp_path, "src/top.sv")
    assert got == PurePosixPath("/work/src/top.sv")


def test_container_path_rejects_escape(tmp_path):
    with pytest.raises(SandboxError):
        container_path(tmp_path, "/etc/passwd")
