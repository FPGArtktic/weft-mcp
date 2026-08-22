# SPDX-License-Identifier: GPL-3.0-only
"""Getting text out of a PDF.

Two paths, chosen per page rather than per document: a page with a text layer
is extracted, a page without one is rendered and read by Tesseract. Mixed
documents are ordinary -- a scanned figure in an otherwise digital standard --
so the decision cannot be made once for the whole file.

Both tools live in weft-tools; the host needs neither.
"""

import shlex
from dataclasses import dataclass
from pathlib import Path

from .. import podman
from ..sandbox import container_path

#: Below this many characters a page is treated as having no text layer. A
#: genuinely scanned page yields nothing; a sparse one yields a page number.
TEXT_THRESHOLD = 24

#: Resolution for rendering a page before OCR. Below 200 dpi Tesseract starts
#: losing small type, which is most of a standard.
OCR_DPI = 300

DEFAULT_TIMEOUT = 1800.0

#: Separates the per-page OCR results in the single container run.
MARKER = "===weft-page==="


class IngestError(RuntimeError):
    """The document cannot be read."""


@dataclass(frozen=True)
class Page:
    """One page of a document.

    @number: 1-based page number
    @text: what was extracted
    @ocr: whether the text came from OCR rather than a text layer
    """

    number: int
    text: str
    ocr: bool


def extract(
    image: str,
    workspace: Path,
    path: str,
    language: str = "eng",
    timeout: float | None = DEFAULT_TIMEOUT,
) -> list[Page]:
    """extract - the text of every page of a PDF

    @image: weft-tools image name
    @workspace: sandbox root
    @path: workspace-relative path to the PDF
    @language: Tesseract language for the pages that need OCR
    @timeout: wall-clock limit for each container run

    Return: one Page per page, in order.

    Raises IngestError if the document has no pages, SandboxError if @path
    escapes the workspace, PodmanError if the container could not start.
    """
    inside = container_path(workspace, path)
    result = podman.run(image, workspace, ["pdftotext", str(inside), "-"], timeout=timeout)
    if result.returncode != 0:
        raise IngestError(f"cannot read {path}: {' '.join(result.output.split())[:200]}")

    # pdftotext ends the last page with a form feed too, hence the trailing
    # empty piece that is dropped here.
    pieces = result.output.split("\f")
    if pieces and not pieces[-1].strip():
        pieces.pop()
    if not pieces:
        raise IngestError(f"no pages in {path}")

    pages = [Page(number=i + 1, text=t, ocr=False) for i, t in enumerate(pieces)]

    blank = [p.number for p in pages if len(p.text.strip()) < TEXT_THRESHOLD]
    if not blank:
        return pages

    recognised = _ocr(image, workspace, inside, blank, language, timeout)
    return [
        Page(number=p.number, text=recognised[p.number], ocr=True) if p.number in recognised else p
        for p in pages
    ]


def _ocr(
    image: str,
    workspace: Path,
    document: object,
    pages: list[int],
    language: str,
    timeout: float | None,
) -> dict[int, str]:
    """_ocr - read the pages that carry no text layer

    Rendering and recognition happen in one container run: a page at a time
    would pay the container start over and over, and a scanned document is
    exactly the case where there are many.
    """
    quoted = shlex.quote(str(document))
    lines = ["cd /tmp"]
    for number in pages:
        lines += [
            f"pdftoppm -r {OCR_DPI} -f {number} -l {number} -png -singlefile "
            f"{quoted} page 2>/dev/null",
            f"echo {shlex.quote(f'{MARKER} {number}')}",
            f"tesseract page.png - -l {shlex.quote(language)} 2>/dev/null || true",
        ]

    result = podman.run(image, workspace, ["sh", "-c", "\n".join(lines)], timeout=timeout)

    found = {}
    for chunk in result.output.split(MARKER)[1:]:
        head, _, body = chunk.partition("\n")
        try:
            found[int(head.strip())] = body
        except ValueError:
            continue
    return found
