# SPDX-License-Identifier: GPL-2.0-only
"""Tests for project creation and assignments.

No Quartus runs here: the Tcl it would have executed is captured and checked
instead. The real thing is exercised by tests/manual/make_project.py.
"""

import pytest

from weft.quartus import project as projmod
from weft.quartus.install import LITE, Install
from weft.quartus.project import (
    Assignment,
    ProjectError,
    create_project,
    list_projects,
    set_assignments,
)


@pytest.fixture
def install(tmp_path):
    return Install(root=tmp_path / "q", edition=LITE, version="25.1std.0", build="1129")


@pytest.fixture
def tcl(monkeypatch):
    """tcl - capture the script instead of running Quartus."""
    seen = {}

    def fake(install, directory, script, environment):
        seen["script"] = script.read_text()
        seen["cwd"] = directory
        return ""

    monkeypatch.setattr(projmod, "_quartus", fake)
    return seen


def test_global_assignment(install):
    assert (
        Assignment("global", name="DEVICE", value="10M04SAE144A7G").render()
        == "set_global_assignment -name DEVICE 10M04SAE144A7G"
    )


def test_value_with_spaces_is_braced(install):
    assert Assignment("global", name="FAMILY", value="MAX 10").render() == (
        "set_global_assignment -name FAMILY {MAX 10}"
    )


def test_location_assignment(install):
    assert (
        Assignment("location", value="PIN_27", to="clk").render()
        == "set_location_assignment PIN_27 -to clk"
    )


def test_instance_assignment(install):
    assert Assignment("instance", name="IO_STANDARD", value="3.3-V LVTTL", to="clk").render() == (
        "set_instance_assignment -name IO_STANDARD {3.3-V LVTTL} -to clk"
    )


def test_parameter_assignment(install):
    """The fourth form a .qsf can carry, and the one easiest to forget."""
    assert (
        Assignment("parameter", name="WIDTH", value="8", to="u_count").render()
        == "set_parameter -name WIDTH 8 -to u_count"
    )


def test_bus_index_survives_quoting(install):
    assert Assignment("location", value="PIN_27", to="led[3]").render().endswith("-to {led[3]}")


def test_unknown_kind_is_refused(install):
    with pytest.raises(ProjectError, match="unknown assignment kind"):
        Assignment("magic", name="X", value="1").render()


def test_assignment_needing_a_target_says_so(install):
    with pytest.raises(ProjectError, match="needs a target"):
        Assignment("instance", name="IO_STANDARD", value="LVTTL").render()


def test_a_bad_assignment_name_is_refused(install):
    with pytest.raises(ProjectError, match="not a usable Quartus name"):
        Assignment("global", name="DEVICE; puts hi", value="x").render()


@pytest.mark.parametrize("value", ["a{b", "a}b", "back\\slash", "two\nlines"])
def test_values_that_cannot_be_quoted_are_refused(install, value):
    """Braces stop substitution; a value carrying them cannot be brace quoted,
    and escaping it would only ever enable an injection."""
    with pytest.raises(ProjectError, match="cannot be quoted"):
        Assignment("global", name="X", value=value).render()


def test_create_project_writes_the_expected_tcl(install, tmp_path, tcl):
    create_project(install, tmp_path / "p", "demo", "MAX 10", "10M04SAE144A7G", top="tiny")
    script = tcl["script"]
    assert "project_new demo -revision demo" in script
    assert "-overwrite" not in script
    assert "set_global_assignment -name FAMILY {MAX 10}" in script
    assert "set_global_assignment -name TOP_LEVEL_ENTITY tiny" in script
    assert script.rstrip().endswith("project_close")
    assert "export_assignments" in script


def test_create_project_defaults_the_top_entity_to_the_name(install, tmp_path, tcl):
    create_project(install, tmp_path / "p", "demo", "MAX 10", "10M04SAE144A7G")
    assert "set_global_assignment -name TOP_LEVEL_ENTITY demo" in tcl["script"]


def test_overwrite_is_opt_in(install, tmp_path, tcl):
    create_project(install, tmp_path / "p", "demo", "MAX 10", "10M04", overwrite=True)
    assert "project_new demo -revision demo -overwrite" in tcl["script"]


def test_the_project_name_stays_relative(install, tmp_path, tcl):
    """An absolute name moves the Tcl interpreter's working directory, and it
    stays moved after project_close."""
    directory = tmp_path / "p"
    create_project(install, directory, "demo", "MAX 10", "10M04")
    assert str(directory) not in tcl["script"]
    assert tcl["cwd"] == directory


def test_set_assignments_opens_rather_than_creates(install, tmp_path, tcl):
    directory = tmp_path / "p"
    directory.mkdir()
    (directory / "demo.qpf").write_text("")
    set_assignments(install, directory, "demo", [Assignment("global", name="X", value="1")])
    assert "project_open demo -revision demo -force" in tcl["script"]
    assert "project_new" not in tcl["script"]


def test_set_assignments_needs_a_project(install, tmp_path, tcl):
    with pytest.raises(ProjectError, match="no project demo"):
        set_assignments(install, tmp_path, "demo", [Assignment("global", name="X", value="1")])


def test_set_assignments_needs_assignments(install, tmp_path, tcl):
    (tmp_path / "demo.qpf").write_text("")
    with pytest.raises(ProjectError, match="no assignments"):
        set_assignments(install, tmp_path, "demo", [])


def test_list_projects_finds_them_anywhere(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "one.qpf").write_text("")
    (tmp_path / "b" / "deep").mkdir(parents=True)
    (tmp_path / "b" / "deep" / "two.qpf").write_text("")
    (tmp_path / "not-a-project.txt").write_text("")
    assert list_projects(tmp_path) == ["a/one.qpf", "b/deep/two.qpf"]
