<!-- SPDX-License-Identifier: GPL-3.0-only -->

# Changelog

Notable changes, newest first. Versions follow [semantic versioning][semver],
and each released version is an annotated tag: `git show v0.1.0`.

Milestones from `PROJECT.md` map onto minor versions while WEFT is below 1.0.
The full detail lives in the git history, which is the authority; this file is
the summary you can read in a minute.

[semver]: https://semver.org/spec/v2.0.0.html

## [0.4.0] — 2026-08-22

Milestone 4 — document retrieval with OCR.

### Added

- `rag`: `index_document`, reading a PDF page by page. Whether a page needs OCR
  is decided per page, not per document: a page with a text layer is taken from
  `pdftotext`, and only a page that comes back nearly empty is rendered and
  passed to Tesseract. Scanned documents therefore work, and born-digital ones
  do not pay for it — IEEE 1800-2017 is 1315 pages and needed OCR on none of
  them, which is three seconds rather than an hour.
- `rag`: clause-aware chunking. A standard is cut at its own headings, so a
  citation names `1800-2017 §9.2.2.4` and a reader can look it up; a chunk
  number could not be checked against anything. Of 2850 chunks from IEEE
  1800-2017, 2727 carry a clause. The designation itself is read from the
  running heads, which are also removed — left in, the document's title would
  sit in every chunk and match every search for it.
- `rag`: `search_docs` and `list_indexed_docs`. A result states which retrieval
  answered it. That is not decoration: a caller told the ordering was semantic
  when it was a substring match would trust a ranking that does not mean what
  it looks like.
- `rag`: embeddings from BGE-M3 through ONNX Runtime, which keeps PyTorch and
  a CUDA stack out of a server that must run offline. Vectors live in a
  `sqlite-vec` table beside the chunks, and are written only when a model is
  configured — without one, WEFT still indexes and still retrieves, by text.

### Notes

- Batches are sorted by length before encoding. A batch is padded to its
  longest member, so a mixed batch spends most of its arithmetic on padding:
  measured against IEEE 1800-2017, an unsorted batch of 32 computes 1.67× the
  tokens that are actually there, and a length-sorted one 1.01×. This was
  found by noticing that smaller batches ran *faster*, which should not happen.
- Retrieval was compared on the full standard, 2850 chunks embedded in 32
  minutes on a CPU. Text search answers a query that names a term — `randcase`
  lands on §18.16 — and answers nothing at all when the question is a
  sentence: all four natural-language queries returned zero. Vector search
  answered three of them correctly, including "abstract methods without
  implementation" → §8.21 and "weighted random selection" → §18.16. The fourth,
  "how do I model a flip-flop", has no good answer in that document: the
  standard defines the language and never sets out to teach modelling, and the
  term appears eleven times in passing and in no heading.

### Changed

- The project moves from **GPL-2.0-only to GPL-3.0-only**. Releases up to and
  including 0.3.0 were made under GPLv2; their tags stand as they were. Only
  WEFT's own files changed: the licences of GHDL, Verible and everything else
  in `weft-tools` are their own and are stated as such. Under GPLv3 an
  Apache-2.0 dependency may be combined into this work, which GPLv2 did not
  allow.

## [0.3.0] — 2026-08-22

Milestone 3 — AST indexing across three languages.

### Added

- `index`: `index_project`, reading a directory on demand into a symbol store.
  Verible covers Verilog and SystemVerilog, GHDL covers VHDL; a file whose
  content hash has not changed is not parsed again, and a file a parser rejects
  is named with its complaint rather than silently leaving a gap.
- `index`: `get_hierarchy`, which resolves instance names without regard to
  case. GHDL folds VHDL identifiers to lower case while Verilog keeps its own,
  so a SystemVerilog module instantiating a VHDL entity would otherwise never
  find it. Instances whose module is not indexed are marked unresolved rather
  than dropped.
- `index`: `get_module_info` and `search_code`. Search matches text over module,
  port, parameter and instance names; semantic ranking waits for the embedding
  store.

## [0.2.0] — 2026-08-22

Milestone 2 — Quartus compilation as persistent jobs.

### Added

- `jobs`: SQLite-backed job store that survives a server restart. Jobs run
  through a shell that records the tool's exit status in a marker file,
  because Quartus writes no marker of its own — its `<revision>.done` holds a
  timestamp and nothing else. Cancellation is recorded before the signal is
  sent, so a job killed along with its shell is known to have been cancelled
  rather than merely lost. Process identity is checked against the start time
  from `/proc`, and a zombie counts as dead.
- `quartus`: `start_compile`, running the whole flow or one stage as a
  persistent job, with progress read from the phases the tool announces in its
  own log. A single stage runs its own executable rather than going through
  the flow, so nothing else is re-run on the way.
- `quartus`: `create_project`, `set_assignments` and `list_projects`, driving
  the `::quartus::project` Tcl API rather than writing `.qsf` text. All four
  assignment forms are supported, `set_parameter` included. Client-supplied
  values are brace quoted, and a value carrying braces or a backslash is
  refused rather than escaped.
- `quartus`: `parse_reports`, folding timing to the worst corner and returning
  a few kilobytes of JSON in place of megabytes of `.rpt`. Reports are looked
  for wherever `PROJECT_OUTPUT_DIRECTORY` says, not in an assumed directory.
- `quartus`: `get_project_info`, read straight from the `.qsf` so it needs no
  Quartus and no second of Tcl startup.
- `quartus`: installation probe. Edition comes from the version banner, not
  from which binaries are present: a Lite installation ships the Pro-only
  `quartus_syn` as a stub that refuses to run, so testing for the file would
  misidentify it.

### Fixed

- `jobs`: the store opened one SQLite connection at startup, which the MCP
  server's worker thread could not use. Connections are per thread now.

## [0.1.0] — 2026-08-22

Milestone 1 — the fast loop.

### Added

- `server`: MCP server over stdio and Streamable HTTP, the latter behind a
  static bearer token.
- `fastloop`: `lint` using Verilator for Verilog and SystemVerilog and GHDL
  for VHDL; `simulate` using Verilator, Icarus Verilog or GHDL, with waveform
  capture. A source set spanning both languages is narrowed to the language of
  the testbench, and the files left out are named in the result.
- `server`: workspace sandbox. Every client-supplied path is resolved before
  it is checked, so a symlink inside the workspace pointing out of it is
  refused.
- `server`: TOML configuration. Quartus paths always come from it; unknown
  keys are refused rather than ignored.
- `containers`: the `weft-tools` image definition. Built by each user and
  never distributed, which keeps WEFT's own distribution to project code alone.
- `Documentation`: the counter demonstration project, written in
  SystemVerilog, Verilog-2001 and VHDL at once.

[0.4.0]: https://github.com/FPGArtktic/weft-mcp/releases/tag/v0.4.0
[0.3.0]: https://github.com/FPGArtktic/weft-mcp/releases/tag/v0.3.0
[0.2.0]: https://github.com/FPGArtktic/weft-mcp/releases/tag/v0.2.0
[0.1.0]: https://github.com/FPGArtktic/weft-mcp/releases/tag/v0.1.0
