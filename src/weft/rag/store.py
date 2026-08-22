# SPDX-License-Identifier: GPL-3.0-only
"""Where indexed documents and their chunks live.

Chunks are stored whatever happens; vectors are stored when an embedding model
is configured. The two searches are separate on purpose, and a result says
which one answered it: a caller told that retrieval was semantic when it was a
substring match would trust an ordering that does not mean what it looks like.
"""

import sqlite3
import struct
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .chunk import Chunk

#: BGE-M3's native width. Stored with the table, so a database built with one
#: model is not silently queried with another.
DEFAULT_DIMENSIONS = 1024

TEXT = "text"
VECTOR = "vector"

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    path        TEXT PRIMARY KEY,
    title       TEXT,
    designation TEXT,
    doc_type    TEXT,
    pages       INTEGER NOT NULL,
    ocr_pages   INTEGER NOT NULL,
    chunks      INTEGER NOT NULL,
    dimensions  INTEGER,
    indexed_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    document TEXT NOT NULL,
    ordinal  INTEGER NOT NULL,
    page     INTEGER NOT NULL,
    clause   TEXT,
    heading  TEXT,
    text     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_document ON chunks (document, ordinal);
CREATE INDEX IF NOT EXISTS chunks_clause ON chunks (clause);
"""


@dataclass(frozen=True)
class Document:
    """One indexed document.

    @path: workspace-relative path
    @title: the PDF's own title, when it has one
    @designation: standard number, e.g. "1800-2017", for citations
    @doc_type: caller's label -- standard, handbook, user_guide
    @pages / @ocr_pages: how many pages, and how many needed OCR
    @chunks: how many pieces it was cut into
    @dimensions: embedding width, or None when it was indexed without a model
    @indexed_at: seconds since the epoch
    """

    path: str
    title: str | None
    designation: str | None
    doc_type: str | None
    pages: int
    ocr_pages: int
    chunks: int
    dimensions: int | None
    indexed_at: float


@dataclass(frozen=True)
class Passage:
    """One retrieved chunk.

    @document: path it came from
    @citation: how to refer to it, clause number where there is one
    @page: page it starts on
    @clause / @heading: the structure it belongs to
    @text: the content
    @score: distance for a vector search, absent for a text search
    """

    document: str
    citation: str
    page: int
    clause: str | None
    heading: str | None
    text: str
    score: float | None = None


class DocumentStore:
    """SQLite-backed document and chunk store, with optional vectors.

    Connections are per thread, as the server runs tools off the thread that
    built the store.
    """

    def __init__(self, database: Path, dimensions: int = DEFAULT_DIMENSIONS):
        """__init__ - open, creating the schema if the database is new.

        @database: SQLite file
        @dimensions: embedding width for the vector table
        """
        self._database = Path(database)
        self._database.parent.mkdir(parents=True, exist_ok=True)
        self._dimensions = dimensions
        self._local = threading.local()
        self._db.executescript(SCHEMA)

    @property
    def _db(self) -> sqlite3.Connection:
        """_db - this thread's connection, with sqlite-vec loaded if available."""
        connection = getattr(self._local, "connection", None)
        if connection is None:
            connection = sqlite3.connect(self._database, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            self._local.connection = connection
            self._local.vectors = _load_vectors(connection, self._dimensions)
        return connection

    @property
    def vectors_available(self) -> bool:
        """vectors_available - whether sqlite-vec loaded for this thread."""
        self._db  # noqa: B018  - opening the connection sets the flag
        return bool(getattr(self._local, "vectors", False))

    def close(self) -> None:
        """close - release this thread's handle."""
        connection = getattr(self._local, "connection", None)
        if connection is not None:
            connection.close()
            self._local.connection = None

    def add(
        self,
        path: str,
        chunks: list[Chunk],
        pages: int,
        ocr_pages: int = 0,
        title: str | None = None,
        designation: str | None = None,
        doc_type: str | None = None,
        vectors: list[list[float]] | None = None,
    ) -> Document:
        """add - record a document, replacing any earlier copy of it

        @path: workspace-relative path, the document's identity
        @chunks: its pieces, in order
        @pages / @ocr_pages: page counts, for reporting
        @title / @designation / @doc_type: metadata for citations and filtering
        @vectors: one embedding per chunk, or None to store text only

        Raises ValueError if @vectors is given but does not match @chunks.

        Return: the stored Document.
        """
        if vectors is not None and len(vectors) != len(chunks):
            raise ValueError(f"{len(vectors)} vectors for {len(chunks)} chunks")

        self.forget(path)
        for chunk in chunks:
            cursor = self._db.execute(
                "INSERT INTO chunks (document, ordinal, page, clause, heading, text)"
                " VALUES (?,?,?,?,?,?)",
                (path, chunk.ordinal, chunk.page, chunk.clause, chunk.heading, chunk.text),
            )
            if vectors is not None and self.vectors_available:
                self._db.execute(
                    "INSERT INTO vec_chunks (id, embedding) VALUES (?,?)",
                    (cursor.lastrowid, _blob(vectors[chunk.ordinal])),
                )

        stored = Document(
            path=path,
            title=title,
            designation=designation,
            doc_type=doc_type,
            pages=pages,
            ocr_pages=ocr_pages,
            chunks=len(chunks),
            dimensions=self._dimensions if vectors is not None else None,
            indexed_at=time.time(),
        )
        self._db.execute(
            "INSERT OR REPLACE INTO documents (path, title, designation, doc_type, pages,"
            " ocr_pages, chunks, dimensions, indexed_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                stored.path,
                stored.title,
                stored.designation,
                stored.doc_type,
                stored.pages,
                stored.ocr_pages,
                stored.chunks,
                stored.dimensions,
                stored.indexed_at,
            ),
        )
        return stored

    def forget(self, path: str) -> None:
        """forget - remove a document and everything it contributed."""
        if self.vectors_available:
            self._db.execute(
                "DELETE FROM vec_chunks WHERE id IN (SELECT id FROM chunks WHERE document = ?)",
                (path,),
            )
        self._db.execute("DELETE FROM chunks WHERE document = ?", (path,))
        self._db.execute("DELETE FROM documents WHERE path = ?", (path,))

    def documents(self) -> list[Document]:
        """documents - everything indexed, newest first."""
        rows = self._db.execute("SELECT * FROM documents ORDER BY indexed_at DESC").fetchall()
        return [
            Document(
                path=r["path"],
                title=r["title"],
                designation=r["designation"],
                doc_type=r["doc_type"],
                pages=r["pages"],
                ocr_pages=r["ocr_pages"],
                chunks=r["chunks"],
                dimensions=r["dimensions"],
                indexed_at=r["indexed_at"],
            )
            for r in rows
        ]

    def search_text(self, query: str, limit: int = 8, doc_type: str | None = None) -> list[Passage]:
        """search_text - chunks containing @query

        Matching is a substring over the chunk, its clause and its heading. It
        finds what it is asked for and nothing near it.
        """
        sql = (
            "SELECT c.*, d.designation FROM chunks c JOIN documents d ON d.path = c.document"
            " WHERE (lower(c.text) LIKE :q OR lower(COALESCE(c.heading,'')) LIKE :q"
            "        OR COALESCE(c.clause,'') LIKE :q)"
        )
        params: dict[str, object] = {"q": f"%{query.lower()}%", "limit": limit}
        if doc_type:
            sql += " AND d.doc_type = :doc_type"
            params["doc_type"] = doc_type
        params["limit"] = max(limit * 20, 100)
        rows = list(self._db.execute(sql, params))
        rows.sort(key=lambda r: -_relevance(r, query.lower()))
        return [_passage(r) for r in rows[:limit]]

    def search_vector(
        self, vector: list[float], limit: int = 8, doc_type: str | None = None
    ) -> list[Passage]:
        """search_vector - the chunks nearest @vector

        Raises RuntimeError when sqlite-vec is not loaded, rather than falling
        back quietly: a caller expecting semantic retrieval should be told it
        did not happen.
        """
        if not self.vectors_available:
            raise RuntimeError("sqlite-vec is not available; vector search cannot run")

        nearest = self._db.execute(
            "SELECT id, distance FROM vec_chunks WHERE embedding MATCH ? AND k = ?"
            " ORDER BY distance",
            (_blob(vector), limit * 4 if doc_type else limit),
        ).fetchall()
        if not nearest:
            return []

        order = {row["id"]: row["distance"] for row in nearest}
        marks = ",".join("?" for _ in order)
        sql = (
            f"SELECT c.*, d.designation FROM chunks c JOIN documents d ON d.path = c.document"
            f" WHERE c.id IN ({marks})"
        )
        params = list(order)
        if doc_type:
            sql += " AND d.doc_type = ?"
            params.append(doc_type)

        found = [_passage(r, score=order[r["id"]]) for r in self._db.execute(sql, params)]
        found.sort(key=lambda p: p.score if p.score is not None else 0.0)
        return found[:limit]


def _relevance(row: sqlite3.Row, query: str) -> float:
    """_relevance - how well a chunk answers @query, for the textual search

    Document order is not an ordering at all: the first chunk that happens to
    mention a term wins, which for a standard means the table of contents beats
    the clause that defines it. Counting occurrences and weighting the heading
    fixes the worst of that. It is still not semantic ranking, and the result
    says so.
    """
    text = (row["text"] or "").lower()
    heading = (row["heading"] or "").lower()

    score = float(text.count(query))
    if query in heading:
        score += 12.0
    if (row["clause"] or "") == query:
        score += 20.0
    # A term is more telling in a short passage than in a long one.
    return score * (600.0 / max(len(text), 600.0))


def _passage(row: sqlite3.Row, score: float | None = None) -> Passage:
    """_passage - a chunk row as something quotable."""
    designation = row["designation"]
    if row["clause"] and designation:
        citation = f"{designation} §{row['clause']}"
    elif row["clause"]:
        citation = f"§{row['clause']}"
    elif row["heading"]:
        # A generated document has headings and no pages worth naming. The
        # PDF chunker only ever records a heading together with its clause,
        # so this branch belongs to generated documentation alone.
        citation = f"{Path(row['document']).name} — {row['heading']}"
    else:
        citation = f"{Path(row['document']).name} p. {row['page']}"

    return Passage(
        document=row["document"],
        citation=citation,
        page=row["page"],
        clause=row["clause"],
        heading=row["heading"],
        text=row["text"],
        score=score,
    )


def _blob(vector: list[float]) -> bytes:
    """_blob - the packed float32 form sqlite-vec stores."""
    return struct.pack(f"{len(vector)}f", *vector)


def _load_vectors(connection: sqlite3.Connection, dimensions: int) -> bool:
    """_load_vectors - load sqlite-vec and make the vector table

    Returns False rather than raising when the extension is unavailable: the
    chunks are still worth storing and still searchable as text.
    """
    try:
        import sqlite_vec
    except ImportError:
        return False

    try:
        connection.enable_load_extension(True)
        sqlite_vec.load(connection)
        connection.enable_load_extension(False)
        connection.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING"
            f" vec0(id INTEGER PRIMARY KEY, embedding float[{dimensions}])"
        )
    except (sqlite3.OperationalError, AttributeError):
        return False
    return True
