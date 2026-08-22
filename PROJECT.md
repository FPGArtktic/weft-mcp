# WEFT — WEFT Elaborates FPGA Toolchains

**An MCP server for the Intel Quartus Prime 25.1 flow — project document, v1.1 — 2026-08-22**

The name is a GNU-style recursive acronym. The *weft* is the thread woven across the warp to make **fabric** — and routing logic into FPGA fabric is precisely the job. Repository name: `weft`. The name deliberately avoids the "Quartus" and "Altera" trademarks; the README states the project is not affiliated with or endorsed by Intel/Altera.

## 1. Overview

WEFT is an open-source MCP (Model Context Protocol) server that gives LLM clients a complete, safe interface to an FPGA development flow built around Intel Quartus Prime 25.1. It combines four capabilities in one server:

1. **Quartus project management and compilation** (Lite/Standard and Pro editions). Quartus is installed **on the host**; WEFT drives its command-line tools directly, with a persistent asynchronous job queue and machine-readable JSON reports.
2. **A fast iteration loop** running in a Podman container (`weft-tools`, Arch Linux based): Verilator, Icarus Verilog, GHDL for linting and behavioral simulation — error feedback in seconds instead of a full Quartus flow.
3. **A local RAG pipeline with OCR** over English-language documentation (Intel/Altera handbooks, IEEE language standards) and over the user's own HDL sources, using BGE-M3 embeddings and sqlite-vec.
4. **Documentation generation** for HDL projects, assembled from parsed ASTs (Verible/GHDL, also containerized), constraint files, and compilation reports.

The primary client is Claude (Desktop / Code) over MCP stdio or Streamable HTTP. A future air-gapped deployment (local open-weights model behind an OpenAI-compatible endpoint) reuses the same HTTP transport and requires no server changes; it is explicitly out of scope for development (see Appendix A).

## 2. Goals and non-goals

### Goals
- Every step of the RTL → bitstream → programmed device flow reachable through MCP tools.
- Long-running work (compilation) modeled as persistent jobs that survive server restarts.
- Compact, structured tool results (JSON summaries, not raw logs) to conserve client context.
- Grounded answers: documentation search returns source citations (file, page, IEEE clause).
- Fully offline-capable at runtime — no network access needed once installed.

### Non-goals
- The air-gapped inference stack (model serving, agent bridge) — handled by DevOps, Appendix A.
- Installing or containerizing Quartus itself — it is a host prerequisite, installed and licensed by the user.
- Redistribution of copyrighted documentation (IEEE PDFs, Intel handbooks) — the user supplies the corpus.
- Support for non-Intel toolchains (Vivado etc.) in the first release.

## 3. Key decisions

| Area | Decision |
|---|---|
| License | **GPL-3.0-only**, SPDX headers in every file, DCO sign-off (kernel model) |
| Quartus | **Installed on the host**, both Lite/Standard and Pro; WEFT calls host binaries, edition and install paths set in config |
| Open-source tooling | **In Podman**: one `weft-tools` image, **Arch Linux base**, containing Verilator, Icarus Verilog, GHDL, Verible, cocotb |
| Embeddings | **BGE-M3** (dense vectors, 1024-dim, 8k-token context), run locally |
| Vector store | **sqlite-vec** — single-file database, no extra infrastructure |
| Job model | Persistent queue backed by SQLite; jobs survive server restart |
| Reports | Parsed to structured JSON (resources, timing, prioritized warnings) |
| Code indexing | AST-based (Verible for SV/Verilog, GHDL for VHDL) + semantic embeddings |
| Conventions | Linux kernel style: Kernel Maintainer Handbook rules for patches/commits, kernel-doc-style comment headers (Section 8) |
| Hardware programming | quartus_pgm via USB Blaster, directly on the host (jtagd on the host) |

## 4. Functional specification — MCP tools

### 4.1 Project management
- `create_project(name, family, part, edition)` — generate `.qpf`/`.qsf`.
- `set_assignments(project, assignments)` — pin assignments, synthesis options (edits `.qsf`).
- `list_projects()` / `get_project_info(project)`.

### 4.2 Compilation — persistent jobs (host Quartus)
- `start_compile(project, stage)` — stage: `syn` | `fit` | `asm` | `sta` | `full`; returns `job_id`. Runs `quartus_sh` / stage binaries from the configured host installation for the project's edition.
- `get_job_status(job_id)` — `queued` | `running` | `done` | `failed` | `cancelled` + progress.
- `get_job_log(job_id, tail)` — tail of the live log.
- `cancel_job(job_id)`.
- Job state lives in SQLite (job_id, PID, project, stage, log path, timestamps). On restart the server reconciles: PID alive → still running; PID gone → status derived from exit marker and report files.
- Per-job limits: wall-clock timeout always; CPU/memory limits via `systemd-run --user --scope` when available.
- `parse_reports(project)` — JSON: resource usage (ALMs/LUTs, block RAM bits, DSPs, pins), timing per clock domain (Fmax, WNS, TNS), critical warnings ranked by severity, with report file references.

### 4.3 Fast loop (containerized, no Quartus involved; Questa excepted)
- `lint(files, language)` — `verilator --lint-only` (SV/Verilog), `ghdl -a --std=08` (VHDL) inside `weft-tools`; returns structured diagnostics (file, line, severity, message).
- `simulate(files, top, testbench, simulator)` — Verilator / Icarus / GHDL inside `weft-tools`; returns pass/fail, log tail, path to VCD/FST waveform.
- `simulate(..., simulator="questa")` — **host** Questa - Altera Starter FPGA Edition, when a `[questa]` section names it. It is the only simulator here that reads Verilog, SystemVerilog and VHDL in one run, so it is what simulates a mixed-language hierarchy whole rather than a module at a time. Proprietary and licensed, therefore never a default and never containerised. The verdict comes from Questa's `TESTSTATUS`, not from the exit status: `vsim -c` exits 0 after a `$fatal`.

### 4.4 Documentation RAG with OCR
- `index_document(path, doc_type)` — PDF ingestion, executed inside `weft-tools`: direct text extraction (`pdftotext`) when a text layer exists; Tesseract OCR for scanned pages. No OCR or PDF binary is a host prerequisite. Chunks carry metadata: file, page, section heading; IEEE standards additionally carry the clause number (e.g. `1800-2023 §13.4`) for precise citation.
- `search_docs(query, top_k, filter)` — top fragments + source metadata; filter by `doc_type` (standard / handbook / user_guide).
- `list_indexed_docs()`.
- Corpus is user-supplied and never committed to the repository. It lives in its own root,
  configured as `[rag] library`, mounted read-only and separate from the workspace: a
  collection of standards outlives any one project, and the index (`[rag] database`) can be
  placed outside the workspace so one library serves every project.

### 4.5 Project source indexing — AST + semantic, on demand
- `index_project(dir)` — **on demand only**: point it at a directory, it indexes what it finds. No file watching, no automatic triggers. Re-running it refreshes the index (files with an unchanged hash are skipped). Builds two indexes:
  - **Symbol index (AST)**: modules/entities, ports, parameters/generics, instances, definition locations. Parsers run inside `weft-tools`: Verible (SV/Verilog), GHDL (VHDL).
  - **Semantic index**: BGE-M3 embeddings, chunked at module/process boundaries, stored in a separate sqlite-vec collection.
- `search_code(query, top_k)` — semantic search over indexed sources.
- `get_module_info(name)` — ports, parameters, dependencies, file locations.
- `get_hierarchy(top)` — instance tree.
- Indexed file types: `.sv`, `.v`, `.vhd`, plus `.qsf` and `.sdc` (constraints are searchable).

### 4.6 Documentation generation
- `generate_docs(project, format)` — Markdown and/or HTML:
  - port/parameter tables and module descriptions from the AST and header comments,
  - instance hierarchy as a Mermaid diagram,
  - pin map and clock domains from `.qsf`/`.sdc`,
  - resource and timing results from `parse_reports` when a compilation exists.
- `generate_module_doc(module)` — single module.
- Division of labor: the server supplies facts (AST, reports); prose descriptions are written by the client model. Generated documentation is automatically indexed into the RAG store.

### 4.7 Hardware programming (host)
- `list_devices()` — `jtagconfig`, JTAG chain discovery.
- `program_device(sof_file, cable, device_index)` — `quartus_pgm`, executed on the host where `jtagd` runs — no USB passthrough anywhere.

## 5. Architecture

### 5.1 Server
- Python ≥3.11, official MCP Python SDK (FastMCP). Runs on the host (it must reach host Quartus binaries and Podman).
- Transports: **stdio** (local Claude Desktop / Code) and **Streamable HTTP** (LAN; static bearer token). Same tool logic behind both.
- Configuration: single TOML file — Quartus install paths per edition (`quartus.lite.root`, `quartus.pro.root`), default edition, workspace root, container image name, embedding model path, HTTP port, job limits.

### 5.2 Container (Podman, rootless)
- **One image: `weft-tools`**, based on **`archlinux:base`** — every external binary WEFT shells out to, except Quartus itself: Verilator, Icarus Verilog, GHDL, Verible, cocotb, plus Tesseract with its language data and Poppler (`pdftotext`) for the RAG ingest path, plus a Python layer for helper scripts. Packages come from the official repositories where available; the two that do not (GHDL and Verible) are built in a separate builder stage, so the final image carries no AUR helper.
- **The image is never distributed.** The repository ships `containers/Containerfile.weft-tools` and nothing else; every user builds the image locally with `podman build`. WEFT therefore distributes only its own GPL-3.0-only code, never an aggregate of third-party binaries under mixed licences — see §7.
- Invocation: `podman run --rm --network=none -v <workspace>:/work weft-tools …`.
- Only the workspace directory is mounted; all user-supplied paths are validated against the workspace root (path-traversal protection). The same validation applies to host-side Quartus invocations — WEFT never touches files outside the workspace.

### 5.3 RAG runtime
- Embedding and retrieval run in the server process; no dedicated container. Document *ingestion* (PDF text extraction and OCR) is delegated to `weft-tools`, so the host needs no Tesseract or Poppler.
- BGE-M3 through **ONNX Runtime** (MIT), CPU by default, rather than through
  FlagEmbedding. The reason is weight, not licensing: FlagEmbedding is MIT —
  its repository carries the licence, even though its PyPI metadata leaves the
  field empty — but it hard-requires torch, transformers, datasets, accelerate,
  peft and sentencepiece, which is gigabytes of dependency to compute one
  embedding. ONNX Runtime needs none of them and has wheels for the
  interpreters this targets.
- On reading licences: the repository is the authority, not the package
  metadata. An empty `license` field on PyPI means the packaging was not filled
  in, and concluding anything about the project's terms from it is a mistake
  worth naming here so it is not repeated.
- The embedding runtime is a library, not a program WEFT executes, so the rule
  in §5.2 does not reach it and there is nothing to gain by containerising it:
  sqlite-vec has to live in the server process because it is the store, and
  moving only the embedder would mean marshalling text in and vectors out for
  every chunk and mounting the model weights into the container as well. The
  licence argument that puts GHDL and Verible in `weft-tools` does not apply
  here either, because this stack is MIT throughout — sqlite-vec is dual
  MIT/Apache-2.0, and MIT is the one taken.
- sqlite-vec database file in the workspace; separate tables for docs, code, and generated documentation.

## 6. Non-functional requirements
- Offline at runtime: model weights, tessdata, and the container image are local after installation.
- Compact tool outputs: parsed summaries instead of raw logs (raw logs remain on disk, reachable via `get_job_log`).
- Portability: no runtime internet dependency, so the same build deploys to the air-gapped network unchanged.
- Host prerequisites documented in the README: Quartus 25.1 (Lite/Standard and/or Pro), Podman, Python ≥3.11, `jtagd` for programming. The README also documents the one-off `weft-tools` image build, which is the only step that needs network access.
- English README and `Documentation/` tree; CI running lint + unit tests; an example demo project (counter + testbench, Cyclone-class dev board).

## 7. Licensing
- **GPL-3.0-only** for all project code; `COPYING` contains the full GPLv2 text; every source file starts with `// SPDX-License-Identifier: GPL-3.0-only` (or the language-appropriate comment form).
- Contributions follow the **Developer Certificate of Origin** — every commit carries `Signed-off-by:`.
- Python dependencies are fetched at install time by the user and are not vendored into the repository, keeping the GPLv3 distribution limited to project code. (Note, not legal advice: the licence of any dependency bundled into a future binary distribution still has to be reviewed, but GPLv3 removes the sharpest edge the project had under GPLv2 — Apache-2.0 is compatible with GPLv3 in one direction, so Apache-2.0 code may be combined into this work, though not the reverse.)
- WEFT invokes Quartus as separate host programs — the standard "mere aggregation / separate works" situation; no Quartus components are distributed.
- The same reasoning covers `weft-tools`: WEFT *executes* the containerised tools, it never links their code. The tools carry their own licences — GHDL is GPL-2.0-only, Verible is Apache-2.0 — and because the image is built by the user rather than shipped by the project, no combined distribution ever arises. Any AUR helper used during the build stays in the builder stage and is absent from the final image.
- No Intel/Altera files, IEEE PDFs, or Intel documentation in the repository — ever.

## 8. Conventions (Kernel Maintainer Handbook applied)

### 8.1 Commits and patches
- Subject: `subsystem: summary` in imperative mood, ≤ 72 characters, lowercase subsystem prefix matching the source tree (`jobs:`, `rag:`, `quartus:`, `fastloop:`, `index:`, `docs:`, `containers:`, `ci:`).
- Body explains **why**, wrapped at 72 columns; the diff explains what.
- One logical change per commit; no "fix typo in previous commit" — rebase before publishing.
- Every commit: `Signed-off-by: Name <email>` (DCO).
- No merge commits on the main branch; linear history, rebase workflow.

### 8.2 Code style
- Python: PEP 8 enforced by `ruff`; the kernel spirit on top of it — short functions, early returns, no deep nesting, no cleverness, no speculative abstraction ("good taste": eliminate special cases rather than branch around them).
- Errors are handled where they occur; no silent excepts.
- Public functions carry kernel-doc-flavored docstrings: one-line summary, arguments, return value, failure modes.

### 8.3 HDL header convention (kernel-doc adapted)
```systemverilog
// SPDX-License-Identifier: GPL-3.0-only
/**
 * uart_tx - UART transmitter, 8N1, parameterized baud divisor
 * @CLK_HZ:    input clock frequency in Hz
 * @BAUD:      target baud rate
 * @clk:       clock, rising edge active
 * @rst_n:     asynchronous reset, active low
 * @tx_data:   byte to transmit
 * @tx_valid:  pulse to start transmission
 * @tx_ready:  high when idle
 * @txd:       serial output line
 *
 * Start bit is launched on the cycle after @tx_valid && @tx_ready.
 */
module uart_tx #(...) (...);
```
The same structure applies to VHDL entities using `--` comments. `generate_docs` and the AST indexer both consume these headers.

## 9. Roadmap

| Milestone | Content | Exit criterion |
|---|---|---|
| M1 | FastMCP skeleton, config, `weft-tools` image (Arch base), `lint`, `simulate` | Claude fixes a broken testbench end-to-end using only MCP tools |
| M2 | Job queue with persistence, host-Quartus driver, `start_compile`/`get_job_status`/`get_job_log`/`cancel_job`, `parse_reports` | Full compile of the demo project driven by Claude; server restart mid-compile loses nothing |
| M3 | AST indexing (Verible, GHDL), `index_project`, `search_code`, `get_module_info`, `get_hierarchy` | Correct hierarchy and port tables for the demo project |
| M4 | RAG: `index_document` (PDF + OCR), `search_docs` with clause-aware chunking for IEEE standards | Query returns correct clause citation from a user-supplied 1800-2023 PDF |
| M5 | `generate_docs`, `generate_module_doc`; generated docs auto-indexed | Docs for demo project render with hierarchy diagram and pin map |
| M6 | Pro-edition support (paths + FlexLM env), `list_devices`, `program_device`, Streamable HTTP + token | Demo bitstream programmed onto a board via MCP |

## Appendix A — Air-gapped deployment (DevOps hand-off; out of scope)
- Client model: Kimi K2 on **32× NVIDIA H100 80 GB** — 2.56 TB of aggregate HBM. That is enough for the FP8 variant (~1 TB of weights) with the rest left for KV cache, which removes the constraint that shaped the earlier 8-GPU sizing: INT4 (~594 GB) is no longer the only variant that fits, and long contexts and real concurrency stop competing with the weights for memory. Sizing at this scale is a tensor/pipeline-parallel layout question rather than a fit question, and belongs with whoever owns the cluster.
- Cluster shape: 32 H100s is four eight-GPU nodes, which is the form these
  accelerators are sold and racked in. That split is not incidental — it decides
  how the model is cut up. Inside a node the GPUs sit on an NVLink/NVSwitch
  domain roughly an order of magnitude faster than anything between nodes, so
  tensor parallelism, which exchanges activations on every layer, has to stay
  inside one node. Pipeline parallelism only passes activations at stage
  boundaries and tolerates the slower inter-node fabric. The natural mapping is
  therefore tensor-parallel across the eight GPUs of a node and pipeline-parallel
  across the four nodes. Getting this backwards — pipelining inside a node and
  sharding tensors across the fabric — costs far more than the hardware saves,
  and is the usual reason a cluster this size underperforms.
- Inter-node fabric: RDMA, InfiniBand or RoCE. The stage boundaries carry little
  traffic compared with tensor-parallel all-reduces, but they are on the critical
  path of every token, so latency matters more than raw bandwidth here.
- What this buys over the earlier 8-GPU sizing is not speed but headroom: the
  FP8 weights fit without squeezing, KV cache stops being rationed, and long contexts
  and concurrent sessions no longer trade against each other. An agentic client
  driving WEFT is exactly the workload that cares — long tool-call transcripts,
  several sessions at once.
- Exact node counts, memory-per-stage arithmetic and fabric topology belong with
  whoever owns the cluster; nothing above is a requirement WEFT imposes. The
  server sees an HTTP endpoint and nothing else.
- Serving: vLLM or SGLang (both expose an OpenAI-compatible endpoint and ship a K2 tool-call parser); SGLang is recommended for agentic workloads with structured output.
- Agent bridge: OpenAI Agents SDK with overridden `base_url`, or a thin custom tool loop; it connects to the same Streamable HTTP endpoint — zero changes on the MCP server side.
- Offline transfer: Podman image (`podman save/load`), pip wheels, BGE-M3 weights, tessdata, model weights for the inference cluster.
