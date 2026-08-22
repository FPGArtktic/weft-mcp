# SPDX-License-Identifier: GPL-3.0-only
"""Tests for the document store."""

import threading

import pytest

from weft.rag.chunk import Chunk
from weft.rag.store import DocumentStore

DOC = "standards/1800.pdf"


def pieces():
    return [
        Chunk(
            text="The always_ff procedure models sequential logic.",
            page=209,
            clause="9.2.2.4",
            heading="Sequential logic always_ff procedure",
            ordinal=0,
        ),
        Chunk(
            text="An interface class declares methods without implementing them.",
            page=195,
            clause="8.26",
            heading="Interface classes",
            ordinal=1,
        ),
        Chunk(
            text="Unnumbered front matter about always nothing.",
            page=3,
            clause=None,
            heading=None,
            ordinal=2,
        ),
    ]


@pytest.fixture
def store(tmp_path):
    s = DocumentStore(tmp_path / "state" / "docs.sqlite")
    yield s
    s.close()


def loaded(store, **kwargs):
    return store.add(
        DOC, pieces(), pages=1315, designation="1800-2017", doc_type="standard", **kwargs
    )


def test_a_document_is_recorded(store):
    got = loaded(store)
    assert (got.chunks, got.pages, got.designation) == (3, 1315, "1800-2017")
    assert [d.path for d in store.documents()] == [DOC]


def test_reindexing_replaces_rather_than_duplicates(store):
    loaded(store)
    loaded(store)
    assert len(store.documents()) == 1
    assert len(store.search_text("always_ff", limit=10)) == 1


def test_forgetting_a_document_removes_its_chunks(store):
    loaded(store)
    store.forget(DOC)
    assert store.documents() == []
    assert store.search_text("always_ff") == []


def test_a_clause_becomes_a_citation(store):
    """A citation naming a clause can be checked against another edition; one
    naming a page cannot."""
    loaded(store)
    got = store.search_text("always_ff")[0]
    assert got.citation == "1800-2017 §9.2.2.4"


def test_a_chunk_without_a_clause_cites_the_page(store):
    loaded(store)
    got = [p for p in store.search_text("front matter", limit=5)]
    assert got[0].citation == "1800.pdf p. 3"


def test_ranking_prefers_the_clause_that_defines_the_term(store):
    """Document order is not an ordering: the table of contents mentions
    everything and would win every search."""
    loaded(store)
    assert store.search_text("always_ff")[0].clause == "9.2.2.4"


def test_a_heading_match_outranks_a_body_match(store):
    loaded(store)
    assert store.search_text("interface class")[0].clause == "8.26"


def test_filtering_by_document_type(store):
    loaded(store)
    assert store.search_text("always_ff", doc_type="standard")
    assert store.search_text("always_ff", doc_type="handbook") == []


def test_a_document_stored_without_vectors_matches_none(store):
    """Storing text only is allowed; the chunks are still searchable as text.
    A vector search over them simply finds nothing, which is the truth."""
    loaded(store)
    if not store.vectors_available:
        pytest.skip("sqlite-vec is not installed")
    assert store.search_vector([0.1] * 1024) == []


def test_vector_search_refuses_rather_than_falling_back(store, monkeypatch):
    """Quietly answering a semantic query with a substring match would have
    the caller trust an ordering that did not happen."""
    loaded(store)
    monkeypatch.setattr(type(store), "vectors_available", property(lambda self: False))
    with pytest.raises(RuntimeError, match="cannot run"):
        store.search_vector([0.0] * 1024)


def test_vectors_must_match_the_chunks(store):
    with pytest.raises(ValueError, match="2 vectors for 3 chunks"):
        store.add(DOC, pieces(), pages=1, vectors=[[0.0] * 1024] * 2)


def test_vector_search_finds_the_nearest(store):
    if not store.vectors_available:
        pytest.skip("sqlite-vec is not installed")
    vectors = [[1.0] + [0.0] * 1023, [0.0, 1.0] + [0.0] * 1022, [0.0] * 1024]
    store.add(DOC, pieces(), pages=1, designation="1800-2017", vectors=vectors)
    got = store.search_vector([0.0, 1.0] + [0.0] * 1022, limit=1)
    assert got[0].clause == "8.26"
    assert got[0].score is not None


def test_the_store_works_from_another_thread(store):
    loaded(store)
    seen = {}

    def worker():
        seen["hits"] = len(store.search_text("always_ff"))

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    assert seen["hits"] == 1


def test_a_document_with_headings_and_no_clauses_cites_its_heading(store):
    """A generated document has sections; "p. 1" would name nothing."""
    store.add(
        "docs/counter.md",
        [Chunk(text="pin map table", page=1, clause=None, heading="Pin map", ordinal=0)],
        pages=1,
        doc_type="generated",
    )
    assert store.search_text("pin map")[0].citation == "counter.md — Pin map"
