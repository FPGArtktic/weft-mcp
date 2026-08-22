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

extensions = ["myst_parser", "sphinx_copybutton", "sphinx_design"]

myst_enable_extensions = ["colon_fence", "deflist", "linkify", "substitution"]
myst_heading_anchors = 3

templates_path = []
exclude_patterns = ["_build"]

html_theme = "furo"
html_title = f"WEFT {release}"
html_static_path = ["_static"]
html_css_files = ["weft.css"]
html_logo = "_static/weft.svg"
html_favicon = "_static/weft.svg"
html_copy_source = False
html_show_sourcelink = False

#: The warp is held under tension and the weft crosses it; the palette is the
#: same two, slate and copper. Both schemes are defined in full rather than
#: one inheriting the other, so a reader in either gets colours that were
#: chosen rather than derived.
html_theme_options = {
    "light_css_variables": {
        "color-brand-primary": "#a2542a",
        "color-brand-content": "#a2542a",
        "color-api-name": "#a2542a",
        "color-api-pre-name": "#7a3f20",
        "color-admonition-title--note": "#3f5a73",
        "color-admonition-title-background--note": "#e8eef4",
    },
    "dark_css_variables": {
        "color-brand-primary": "#e0955f",
        "color-brand-content": "#e0955f",
        "color-api-name": "#e0955f",
        "color-api-pre-name": "#c2703a",
        "color-admonition-title--note": "#8fb0cc",
        "color-admonition-title-background--note": "#1e2833",
    },
    "source_repository": "https://github.com/FPGArtktic/weft-mcp/",
    "source_branch": "main",
    "source_directory": "Documentation/",
}

# The generated documents examples/counter/docs holds are output, not sources.
suppress_warnings = ["myst.header"]


def setup(app):
    """setup - write the generated pages before the build reads them.

    A drift between the code and what is documented about it is raised as an
    ExtensionError, so the build says what is out of date instead of printing
    a traceback that invites the reader to report a Sphinx bug.
    """
    from sphinx.errors import ExtensionError

    here = Path(__file__).resolve().parent
    sys.path.insert(0, str(here))
    import generate

    try:
        generate.write(here)
    except ValueError as e:
        raise ExtensionError(f"documentation is out of date with the code: {e}") from e
