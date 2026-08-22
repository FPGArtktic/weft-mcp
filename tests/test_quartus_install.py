# SPDX-License-Identifier: GPL-2.0-only
"""Tests for identifying a Quartus installation.

No Quartus is run here. The Lite banner is captured verbatim from a real
25.1std installation; the Standard and Pro banners are the same shape with
the edition words swapped, which is all the parser looks at.
"""

import pytest

from weft.quartus.install import (
    LITE,
    PRO,
    STANDARD,
    Install,
    InstallError,
    _parse,
    probe,
)

LITE_BANNER = """\
Quartus Prime Shell
Version 25.1std.0 Build 1129 10/21/2025 SC Lite Edition
Copyright (C) 2025  Altera Corporation. All rights reserved.
"""

STANDARD_BANNER = LITE_BANNER.replace("SC Lite Edition", "SC Standard Edition")
PRO_BANNER = LITE_BANNER.replace("SC Lite Edition", "SC Pro Edition")


def test_lite_banner_is_read(tmp_path):
    got = _parse(tmp_path, LITE_BANNER, {})
    assert (got.edition, got.version, got.build) == (LITE, "25.1std.0", "1129")


def test_standard_and_pro_are_told_apart(tmp_path):
    assert _parse(tmp_path, STANDARD_BANNER, {}).edition == STANDARD
    assert _parse(tmp_path, PRO_BANNER, {}).edition == PRO


def test_pro_synthesises_with_quartus_syn(tmp_path):
    """Both binaries exist on Lite, so the edition has to decide."""
    assert _parse(tmp_path, PRO_BANNER, {}).synthesis_tool == "quartus_syn"
    assert _parse(tmp_path, LITE_BANNER, {}).synthesis_tool == "quartus_map"


def test_flexlm_environment_is_carried(tmp_path):
    env = {"LM_LICENSE_FILE": "1800@lic"}
    assert _parse(tmp_path, PRO_BANNER, env).env == env


def test_unreadable_banner_is_an_error(tmp_path):
    with pytest.raises(InstallError, match="cannot read the version banner"):
        _parse(tmp_path, "not a banner at all\n", {})


def test_unknown_edition_is_an_error(tmp_path):
    with pytest.raises(InstallError, match="unknown Quartus edition"):
        _parse(tmp_path, LITE_BANNER.replace("SC Lite Edition", "SC Fictional Edition"), {})


def test_missing_installation_is_an_error(tmp_path):
    with pytest.raises(InstallError, match="no bin/quartus_sh"):
        probe(tmp_path)


def test_tool_paths_stay_inside_the_install(tmp_path):
    """The wrapper derives its root from its own path, so it must be called
    through <root>/bin and never through a symlink placed elsewhere."""
    install = Install(root=tmp_path, edition=LITE, version="25.1std.0", build="1129")
    assert install.tool("quartus_map") == tmp_path / "bin" / "quartus_map"


def test_require_reports_a_missing_tool(tmp_path):
    install = Install(root=tmp_path, edition=LITE, version="25.1std.0", build="1129")
    with pytest.raises(InstallError, match="quartus_pgm is missing"):
        install.require("quartus_pgm")
