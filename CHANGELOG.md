<!-- SPDX-License-Identifier: GPL-2.0-only -->

# Changelog

Notable changes, newest first. Versions follow [semantic versioning][semver],
and each released version is an annotated tag: `git show v0.1.0`.

Milestones from `PROJECT.md` map onto minor versions while WEFT is below 1.0.
The full detail lives in the git history, which is the authority; this file is
the summary you can read in a minute.

[semver]: https://semver.org/spec/v2.0.0.html

## Unreleased

Nothing yet.

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
  never distributed, which keeps WEFT's own distribution to GPL-2.0-only code.
- `Documentation`: the counter demonstration project, written in
  SystemVerilog, Verilog-2001 and VHDL at once.

[0.3.0]: https://github.com/FPGArtktic/weft-mcp/releases/tag/v0.3.0
[0.2.0]: https://github.com/FPGArtktic/weft-mcp/releases/tag/v0.2.0
[0.1.0]: https://github.com/FPGArtktic/weft-mcp/releases/tag/v0.1.0
