# SPDX-License-Identifier: GPL-3.0-only
"""The symbol index: what was found, and where.

Indexing is on demand, so the store has to survive between runs and know what
has already been read. Files carry a content hash; an unchanged file is not
parsed again.

Names are matched without regard to case. VHDL is case-insensitive and GHDL
hands identifiers back folded to lower case, while Verilog is case-sensitive
and keeps them as written. A SystemVerilog module instantiating a VHDL entity
would otherwise never find it, which is exactly what the demonstration project
does twice.
"""

import hashlib
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

from .model import Instance, Module, Parameter, Port

#: How deep a hierarchy may go before it is treated as looping.
MAX_DEPTH = 64

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path      TEXT PRIMARY KEY,
    hash      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS modules (
    name      TEXT NOT NULL,
    folded    TEXT NOT NULL,
    language  TEXT NOT NULL,
    file      TEXT NOT NULL,
    line      INTEGER NOT NULL,
    PRIMARY KEY (folded, file)
);
CREATE TABLE IF NOT EXISTS ports (
    module    TEXT NOT NULL,
    file      TEXT NOT NULL,
    ordinal   INTEGER NOT NULL,
    name      TEXT NOT NULL,
    direction TEXT NOT NULL,
    type      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS parameters (
    module    TEXT NOT NULL,
    file      TEXT NOT NULL,
    ordinal   INTEGER NOT NULL,
    name      TEXT NOT NULL,
    value     TEXT,
    type      TEXT
);
CREATE TABLE IF NOT EXISTS instances (
    module    TEXT NOT NULL,
    file      TEXT NOT NULL,
    ordinal   INTEGER NOT NULL,
    name      TEXT NOT NULL,
    of        TEXT NOT NULL,
    of_folded TEXT NOT NULL,
    line      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS modules_folded ON modules (folded);
CREATE INDEX IF NOT EXISTS instances_module ON instances (module, file);
"""


@dataclass(frozen=True)
class Hit:
    """One search result.

    @module: module or entity the match belongs to
    @file: path relative to the indexed directory
    @line: where the module is declared
    @kind: what matched -- "module", "port", "parameter" or "instance"
    @detail: the matching text
    """

    module: str
    file: str
    line: int
    kind: str
    detail: str


def digest(text: str) -> str:
    """digest - content hash used to skip files that have not changed."""
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


class SymbolStore:
    """SQLite-backed symbol table.

    Connections are per thread, as the MCP server runs tools off the thread
    that built the store.
    """

    def __init__(self, database: Path):
        """__init__ - open, creating the schema if the database is new."""
        self._database = Path(database)
        self._database.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._db.executescript(SCHEMA)

    @property
    def _db(self) -> sqlite3.Connection:
        """_db - this thread's connection, opened on first use."""
        connection = getattr(self._local, "connection", None)
        if connection is None:
            connection = sqlite3.connect(self._database, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            self._local.connection = connection
        return connection

    def close(self) -> None:
        """close - release this thread's handle."""
        connection = getattr(self._local, "connection", None)
        if connection is not None:
            connection.close()
            self._local.connection = None

    def unchanged(self, path: str, hash: str) -> bool:
        """unchanged - whether @path was indexed with this content already."""
        row = self._db.execute("SELECT hash FROM files WHERE path = ?", (path,)).fetchone()
        return row is not None and row["hash"] == hash

    def replace(self, path: str, hash: str, modules: list[Module]) -> None:
        """replace - record what a file declares, discarding what it used to

        @path: file path relative to the indexed directory
        @hash: content hash, so the next run can skip it
        @modules: everything the file declares

        Deleting first is what makes re-indexing a rename or a deletion
        correct: a module that moved out of this file leaves no ghost behind.
        """
        self._forget(path)
        for module in modules:
            self._db.execute(
                "INSERT OR REPLACE INTO modules (name, folded, language, file, line)"
                " VALUES (?,?,?,?,?)",
                (module.name, module.name.lower(), module.language, path, module.line),
            )
            self._db.executemany(
                "INSERT INTO ports (module, file, ordinal, name, direction, type)"
                " VALUES (?,?,?,?,?,?)",
                [
                    (module.name, path, i, p.name, p.direction, p.type)
                    for i, p in enumerate(module.ports)
                ],
            )
            self._db.executemany(
                "INSERT INTO parameters (module, file, ordinal, name, value, type)"
                " VALUES (?,?,?,?,?,?)",
                [
                    (module.name, path, i, p.name, p.default, p.type)
                    for i, p in enumerate(module.parameters)
                ],
            )
            self._db.executemany(
                "INSERT INTO instances (module, file, ordinal, name, of, of_folded, line)"
                " VALUES (?,?,?,?,?,?,?)",
                [
                    (module.name, path, i, x.name, x.of, x.of.lower(), x.line)
                    for i, x in enumerate(module.instances)
                ],
            )
        self._db.execute("INSERT OR REPLACE INTO files (path, hash) VALUES (?,?)", (path, hash))

    def forget_all_but(self, present: set[str]) -> list[str]:
        """forget_all_but - drop files that are no longer on disk

        Return: the paths that were dropped.
        """
        rows = self._db.execute("SELECT path FROM files").fetchall()
        gone = [r["path"] for r in rows if r["path"] not in present]
        for path in gone:
            self._forget(path)
            self._db.execute("DELETE FROM files WHERE path = ?", (path,))
        return gone

    def module(self, name: str) -> Module | None:
        """module - one module by name, matched without regard to case

        Return: the Module, or None when nothing of that name is indexed.
        """
        row = self._db.execute(
            "SELECT * FROM modules WHERE folded = ? ORDER BY file LIMIT 1", (name.lower(),)
        ).fetchone()
        return None if row is None else self._build(row)

    def modules(self) -> list[Module]:
        """modules - everything indexed, by name."""
        rows = self._db.execute("SELECT * FROM modules ORDER BY folded, file").fetchall()
        return [self._build(r) for r in rows]

    def dependents(self, name: str) -> list[str]:
        """dependents - modules that instantiate @name."""
        rows = self._db.execute(
            "SELECT DISTINCT module FROM instances WHERE of_folded = ? ORDER BY module",
            (name.lower(),),
        ).fetchall()
        return [r["module"] for r in rows]

    def search(self, query: str, limit: int = 20) -> list[Hit]:
        """search - modules whose name, ports, parameters or instances match

        @query: substring, matched without regard to case
        @limit: how many hits to return

        Matching is textual over the symbol index. Semantic ranking needs the
        embedding store, which arrives with the document tools; until then
        this says what it does rather than pretending to rank by meaning.

        Return: hits, modules first, then ports, parameters and instances.
        """
        like = f"%{query.lower()}%"
        hits: list[Hit] = []

        for row in self._db.execute(
            "SELECT name, file, line FROM modules WHERE folded LIKE ? ORDER BY name", (like,)
        ):
            hits.append(Hit(row["name"], row["file"], row["line"], "module", row["name"]))

        for kind, sql in (
            (
                "port",
                "SELECT p.module, p.file, m.line, p.name, p.direction, p.type FROM ports p"
                " JOIN modules m ON m.name = p.module AND m.file = p.file"
                " WHERE lower(p.name) LIKE ? OR lower(p.type) LIKE ? ORDER BY p.module, p.ordinal",
            ),
            (
                "parameter",
                "SELECT p.module, p.file, m.line, p.name, p.value AS direction,"
                " COALESCE(p.type,'') AS type FROM parameters p"
                " JOIN modules m ON m.name = p.module AND m.file = p.file"
                " WHERE lower(p.name) LIKE ? OR lower(COALESCE(p.type,'')) LIKE ?"
                " ORDER BY p.module, p.ordinal",
            ),
            (
                "instance",
                "SELECT i.module, i.file, m.line, i.name, i.of AS direction, '' AS type"
                " FROM instances i JOIN modules m ON m.name = i.module AND m.file = i.file"
                " WHERE lower(i.name) LIKE ? OR i.of_folded LIKE ? ORDER BY i.module, i.ordinal",
            ),
        ):
            for row in self._db.execute(sql, (like, like)):
                detail = f"{row['name']} {row['direction'] or ''} {row['type'] or ''}".strip()
                hits.append(Hit(row["module"], row["file"], row["line"], kind, detail))

        return hits[:limit]

    def hierarchy(self, top: str) -> dict | None:
        """hierarchy - the instance tree below @top

        @top: module or entity name, matched without regard to case

        An instance whose module is not indexed appears with resolved false
        rather than being dropped: a hierarchy with a hole in it is a useful
        answer, and a silently pruned one is not.

        Return: nested dicts, or None when @top is not indexed.
        """
        if self.module(top) is None:
            return None
        return self._branch(top, set(), 0)

    def stats(self) -> dict[str, int]:
        """stats - how much is indexed."""
        counts = {}
        for table in ("files", "modules", "ports", "parameters", "instances"):
            counts[table] = self._db.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        return counts

    def _forget(self, path: str) -> None:
        """_forget - remove every row a file contributed."""
        for table in ("modules", "ports", "parameters", "instances"):
            self._db.execute(f"DELETE FROM {table} WHERE file = ?", (path,))

    def _build(self, row: sqlite3.Row) -> Module:
        """_build - a Module and its children, from a modules row."""
        name, file = row["name"], row["file"]
        ports = [
            Port(r["name"], r["direction"], r["type"])
            for r in self._db.execute(
                "SELECT * FROM ports WHERE module = ? AND file = ? ORDER BY ordinal", (name, file)
            )
        ]
        parameters = [
            Parameter(r["name"], r["value"], r["type"])
            for r in self._db.execute(
                "SELECT * FROM parameters WHERE module = ? AND file = ? ORDER BY ordinal",
                (name, file),
            )
        ]
        instances = [
            Instance(r["name"], r["of"], r["line"])
            for r in self._db.execute(
                "SELECT * FROM instances WHERE module = ? AND file = ? ORDER BY ordinal",
                (name, file),
            )
        ]
        return Module(
            name=name,
            language=row["language"],
            file=file,
            line=row["line"],
            ports=ports,
            parameters=parameters,
            instances=instances,
        )

    def _branch(self, name: str, seen: set[str], depth: int) -> dict:
        """_branch - one node of the hierarchy, with its children."""
        module = self.module(name)
        if module is None:
            return {"module": name, "resolved": False, "instances": []}

        folded = module.name.lower()
        if folded in seen or depth >= MAX_DEPTH:
            return {
                "module": module.name,
                "file": module.file,
                "resolved": True,
                "recursive": True,
                "instances": [],
            }

        return {
            "module": module.name,
            "language": module.language,
            "file": module.file,
            "resolved": True,
            "instances": [
                {
                    "name": i.name,
                    "of": i.of,
                    "line": i.line,
                    **self._branch(i.of, seen | {folded}, depth + 1),
                }
                for i in module.instances
            ],
        }
