# SPDX-License-Identifier: GPL-3.0-only
"""Tests for the symbol index."""

import sqlite3
import threading

import pytest

from weft.index.model import IN, OUT, SYSTEMVERILOG, VHDL, Instance, Module, Parameter, Port
from weft.index.store import SymbolStore, digest


@pytest.fixture
def store(tmp_path):
    s = SymbolStore(tmp_path / "state" / "symbols.sqlite")
    yield s
    s.close()


def top(instances=("u_seg",)):
    return Module(
        name="counter_top",
        language=SYSTEMVERILOG,
        file="src/counter_top.sv",
        line=24,
        ports=[Port("clk", IN, "logic"), Port("led", OUT, "logic [7:0]")],
        parameters=[Parameter("CLK_HZ", "50_000_000", "int")],
        instances=[Instance(n, "seven_seg_decoder", 137) for n in instances],
    )


def leaf():
    """leaf - a VHDL entity, whose name GHDL folds to lower case."""
    return Module(
        name="seven_seg_decoder",
        language=VHDL,
        file="src/seven_seg_decoder.vhd",
        line=19,
        ports=[Port("nibble", IN, "std_logic_vector(3 downto 0)")],
    )


def load(store, *modules):
    for m in modules:
        store.replace(m.file, digest(m.file), [m])


def test_a_module_comes_back_whole(store):
    load(store, top())
    got = store.module("counter_top")
    assert got.line == 24
    assert [p.name for p in got.ports] == ["clk", "led"]
    assert got.parameters[0].default == "50_000_000"
    assert got.instances[0].of == "seven_seg_decoder"


def test_lookup_ignores_case(store):
    """GHDL folds VHDL identifiers; Verilog keeps its own. A SystemVerilog
    module instantiating a VHDL entity has to find it anyway."""
    load(store, leaf())
    assert store.module("SEVEN_SEG_DECODER").name == "seven_seg_decoder"
    assert store.module("Seven_Seg_Decoder") is not None


def test_an_unindexed_module_is_absent_not_invented(store):
    assert store.module("nothing_like_this") is None


def test_unchanged_files_are_recognised(store):
    store.replace("a.sv", digest("body"), [])
    assert store.unchanged("a.sv", digest("body"))
    assert not store.unchanged("a.sv", digest("edited"))
    assert not store.unchanged("b.sv", digest("body"))


def test_reindexing_a_file_replaces_what_it_declared(store):
    """A module that moved out of a file must leave no ghost."""
    load(store, top())
    store.replace("src/counter_top.sv", digest("v2"), [])
    assert store.module("counter_top") is None


def test_a_deleted_file_is_dropped(store):
    load(store, top(), leaf())
    gone = store.forget_all_but({"src/seven_seg_decoder.vhd"})
    assert gone == ["src/counter_top.sv"]
    assert store.module("counter_top") is None
    assert store.module("seven_seg_decoder") is not None


def test_dependents(store):
    load(store, top(), leaf())
    assert store.dependents("seven_seg_decoder") == ["counter_top"]
    assert store.dependents("counter_top") == []


def test_hierarchy_crosses_languages(store):
    load(store, top(), leaf())
    tree = store.hierarchy("counter_top")
    assert tree["language"] == SYSTEMVERILOG
    child = tree["instances"][0]
    assert (child["name"], child["module"], child["language"]) == (
        "u_seg",
        "seven_seg_decoder",
        VHDL,
    )


def test_an_unindexed_child_is_marked_not_dropped(store):
    """A hierarchy with a hole in it is useful; a silently pruned one is not."""
    load(store, top())
    child = store.hierarchy("counter_top")["instances"][0]
    assert child["resolved"] is False
    assert child["module"] == "seven_seg_decoder"


def test_hierarchy_of_an_unindexed_top(store):
    assert store.hierarchy("nothing") is None


def test_a_cycle_does_not_recurse_forever(store):
    """Nothing legal instantiates itself, but a broken index must not hang."""
    loop = Module(
        name="a", language=SYSTEMVERILOG, file="a.sv", line=1, instances=[Instance("u", "a", 2)]
    )
    load(store, loop)
    tree = store.hierarchy("a")
    assert tree["instances"][0]["recursive"] is True


def test_search_finds_modules_ports_and_instances(store):
    load(store, top(), leaf())
    kinds = {h.kind for h in store.search("seg")}
    assert kinds == {"module", "instance"}
    assert any(h.kind == "port" for h in store.search("nibble"))


def test_search_matches_types_too(store):
    load(store, leaf())
    assert any("std_logic_vector" in h.detail for h in store.search("std_logic_vector"))


def test_search_respects_the_limit(store):
    load(store, top(instances=tuple(f"u{i}" for i in range(30))))
    assert len(store.search("u", limit=5)) == 5


def test_the_store_works_from_another_thread(store):
    """The MCP server runs tools off the thread that built the store."""
    load(store, leaf())
    seen = {}

    def worker():
        seen["name"] = store.module("seven_seg_decoder").name

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    assert seen["name"] == "seven_seg_decoder"


def test_an_older_database_gains_the_documentation_columns(tmp_path):
    """A database written before headers were read must not have to be rebuilt."""
    database = tmp_path / "symbols.sqlite"
    old = sqlite3.connect(database)
    old.executescript(
        "CREATE TABLE files (path TEXT PRIMARY KEY, hash TEXT NOT NULL);"
        "CREATE TABLE modules (name TEXT NOT NULL, folded TEXT NOT NULL,"
        " language TEXT NOT NULL, file TEXT NOT NULL, line INTEGER NOT NULL,"
        " PRIMARY KEY (folded, file));"
        "CREATE TABLE ports (module TEXT NOT NULL, file TEXT NOT NULL,"
        " ordinal INTEGER NOT NULL, name TEXT NOT NULL, direction TEXT NOT NULL,"
        " type TEXT NOT NULL);"
        "CREATE TABLE parameters (module TEXT NOT NULL, file TEXT NOT NULL,"
        " ordinal INTEGER NOT NULL, name TEXT NOT NULL, value TEXT, type TEXT);"
        "CREATE TABLE instances (module TEXT NOT NULL, file TEXT NOT NULL,"
        " ordinal INTEGER NOT NULL, name TEXT NOT NULL, of TEXT NOT NULL,"
        " of_folded TEXT NOT NULL, line INTEGER NOT NULL);"
    )
    old.execute("INSERT INTO files VALUES ('a.sv', 'deadbeef')")
    old.execute("INSERT INTO modules VALUES ('old', 'old', 'systemverilog', 'a.sv', 1)")
    old.commit()
    old.close()

    store = SymbolStore(database)
    module = store.module("old")
    assert module is not None
    assert module.summary is None
    assert store.unchanged("a.sv", "deadbeef")
