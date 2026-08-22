# SPDX-License-Identifier: GPL-3.0-only
"""Generate a project reference against a real project.

The unit tests build their facts by hand. This runs the whole path: Verible
and GHDL in the container produce the syntax trees, the .qsf and .sdc supply
the constraints, and the fitter's own reports supply the pin map and the
timing. It needs podman and a project that has been compiled at least once.

    python tests/manual/generate_docs.py <workspace> <project> [top]

<project> is the project name or a workspace-relative .qpf path, as
list_projects returns them.
"""

import sys
import tempfile
from pathlib import Path

from weft.docgen import constraints, document, render
from weft.index.indexer import index_project
from weft.index.store import SymbolStore
from weft.quartus import project as qproject
from weft.quartus import reports as qreports

IMAGE = "localhost/weft-tools:latest"


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__.strip(), file=sys.stderr)
        return 2

    workspace = Path(argv[1]).resolve()
    reference = argv[2]
    directory = workspace / (reference if reference.endswith(".qpf") else f"{reference}")
    directory = directory.parent if reference.endswith(".qpf") else directory
    revision = Path(reference).stem

    with tempfile.TemporaryDirectory() as scratch:
        store = SymbolStore(Path(scratch) / "symbols.sqlite")
        indexed = index_project(IMAGE, workspace, str(directory.relative_to(workspace)), store)
        print(f"indexed {indexed.modules} modules from {len(indexed.parsed)} files")
        for path, why in indexed.failed.items():
            print(f"  FAILED {path}: {why}")

        info = qproject.project_info(directory, revision)
        top = argv[3] if len(argv) > 3 else info.get("top_entity")

        try:
            reports = qreports.parse_reports(directory, revision)
            output = qreports.output_directory(directory, revision)
        except qreports.ReportError as e:
            print(f"no reports: {e}")
            reports, output = None, None

        built = render.Project(
            name=revision,
            info=info,
            modules=store.modules(),
            top=store.hierarchy(top) if top else None,
            pins=constraints.pins(directory, revision, output),
            clocks=constraints.clocks(directory, revision),
            reports=reports,
        )
        blocks = render.project_doc(built)

        for form, suffix in ((document.MARKDOWN, "md"), (document.HTML, "html")):
            path = directory / "docs" / f"{revision}.{suffix}"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(document.render(blocks, form, title=revision))
            print(f"wrote {path} ({path.stat().st_size} bytes)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
