# SPDX-License-Identifier: GPL-3.0-only
"""Sphinx configuration.

The documentation is Markdown, read through MyST, because the repository's
prose already is: README, PROJECT.md and the changelog are included from where
they live rather than copied here. A copy drifts, and the copy is always the
one that is wrong.

The tool reference is generated from the running server at build time for the
same reason. A hand-written list of twenty tools is a list that will disagree
with the code by the next milestone.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from weft import __version__  # noqa: E402

project = "WEFT"
copyright = "2026, Mateusz Okulanis"
author = "Mateusz Okulanis"
release = __version__
version = __version__

extensions = ["myst_parser", "sphinx_copybutton"]

myst_enable_extensions = ["colon_fence", "deflist", "linkify", "substitution"]
myst_heading_anchors = 3

templates_path = []
exclude_patterns = ["_build"]

html_theme = "furo"
html_title = f"WEFT {release}"
html_static_path = []

# The generated documents examples/counter/docs holds are output, not sources.
suppress_warnings = ["myst.header"]


def setup(app):
    """setup - write the generated pages before the build reads them."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import generate

    generate.write(Path(__file__).resolve().parent)
