# SPDX-License-Identifier: GPL-2.0-only
"""Shared fixtures and the container marker."""

import shutil
import subprocess

import pytest

IMAGE = "localhost/weft-tools:latest"


@pytest.fixture
def image() -> str:
    """image - the weft-tools image the container tests run against."""
    return IMAGE


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "container: needs podman and a locally built weft-tools image"
    )


def _image_available() -> bool:
    """_image_available - podman is installed and weft-tools has been built."""
    if shutil.which("podman") is None:
        return False
    done = subprocess.run(
        ["podman", "image", "exists", IMAGE],
        capture_output=True,
        check=False,
    )
    return done.returncode == 0


def pytest_collection_modifyitems(config, items):
    if _image_available():
        return
    skip = pytest.mark.skip(reason="podman or the weft-tools image is unavailable")
    for item in items:
        if "container" in item.keywords:
            item.add_marker(skip)
