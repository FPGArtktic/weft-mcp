# SPDX-License-Identifier: GPL-3.0-only
"""Ingest, chunk and search a real PDF.

The unit tests mock the container and write their pages by hand. This runs the
whole path against a document you supply -- it is never committed, and neither
is anything it produces.

    python tests/manual/index_pdf.py <workspace> <document.pdf> [query ...]
"""

import sys
import tempfile
from pathlib import Path

from weft.rag.chunk import chunks, designation
from weft.rag.ingest import extract
from weft.rag.store import DocumentStore

IMAGE = "localhost/weft-tools:latest"


def main(argv: list[str]) -> int:
    """main - index argv[2] inside workspace argv[1] and run the queries."""
    if len(argv) < 3:
        print(__doc__)
        return 2

    workspace, document = Path(argv[1]), argv[2]
    queries = argv[3:] or ["always_ff", "interface class", "clocking block"]

    pages = extract(IMAGE, workspace, document)
    ocr = sum(1 for p in pages if p.ocr)
    print(f"pages {len(pages)}, of which {ocr} needed OCR")

    pieces = chunks(pages)
    numbered = [c for c in pieces if c.clause]
    print(f"chunks {len(pieces)}, {len(numbered)} carrying a clause number")
    print(f"designation {designation(pages) or '(none)'}")

    with tempfile.TemporaryDirectory() as scratch:
        store = DocumentStore(Path(scratch) / "docs.sqlite")
        print(f"vectors available: {store.vectors_available}")
        store.add(
            document,
            pieces,
            pages=len(pages),
            ocr_pages=ocr,
            designation=designation(pages),
            doc_type="standard",
        )
        for query in queries:
            print(f"\n{query!r}")
            for hit in store.search_text(query, limit=3):
                print(f"  {hit.citation:22} {hit.heading or '-'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
