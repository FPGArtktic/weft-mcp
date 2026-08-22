<!-- SPDX-License-Identifier: GPL-3.0-only -->

# WEFT — WEFT Elaborates FPGA Toolchains

An MCP server that gives an LLM client a safe, structured interface to an
Intel Quartus Prime 25.1 FPGA flow: lint and simulate in seconds, compile
asynchronously, and read results back as JSON instead of megabytes of log.

The name is a GNU-style recursive acronym. The *weft* is the thread woven
across the warp to make **fabric**, and routing logic into FPGA fabric is
precisely the job.

## Status

WEFT is under construction, milestone by milestone. What is finished today:

| Tool | State |
|---|---|
| `lint` | working — Verilator for Verilog and SystemVerilog, GHDL for VHDL |
| `simulate` | working — Verilator, Icarus or GHDL, with waveform capture |
| Quartus projects | working — `create_project`, `set_assignments`, `get_project_info`, `list_projects` |
| Quartus compilation | working — `start_compile` as a persistent job, `get_job_status`, `get_job_log`, `cancel_job` |
| `parse_reports` | working — resources, timing per clock, ranked messages |
| Source indexing | working — `index_project`, `get_module_info`, `get_hierarchy`, `search_code` |
| Document RAG with OCR | working — `index_document`, `search_docs`, `list_indexed_docs`; clause-level citations |
| Documentation generation | working — `generate_docs`, `generate_module_doc`; Markdown and HTML, auto-indexed |
| Device programming | not yet |

Both transports work: stdio for a local client, Streamable HTTP behind a
static bearer token for a client on the LAN.

## What this is for, if MCP is new to you

Say a testbench fails and you want a model's help with it. Today you copy the
file into a chat window, run Verilator yourself, paste a screenful of
`%Warning-WIDTHEXPAND` after it, read the answer, apply the fix by hand, and go
round again. The design is three files deep, so either you paste all three or
the model guesses at the two you left out — and it will guess, confidently. The
answer you get is about the text you pasted, which is not necessarily what is on
disk.

MCP, the Model Context Protocol, removes the ferrying. A server advertises a
list of tools and the arguments each one takes. An LLM client — Claude Desktop,
Claude Code, or anything else that speaks the protocol — puts that list in front
of the model. You keep typing prose. The model picks a tool, fills in the
arguments, and the client sends the call. WEFT is the server on the far end. It
holds no model, runs no inference, and makes no network calls at runtime.

When the model calls `lint`, WEFT resolves every path against your workspace
root, refuses anything that escapes it, and runs roughly this:

```bash
podman run --rm --network=none -v <workspace>:/work -w /work weft-tools \
    verilator --lint-only -Isrc src/updown_counter.sv
```

Verilator prints what it always prints. WEFT turns that into records — file,
line, severity, message — and the container is gone. `simulate` is the same loop
around Verilator, Icarus or GHDL, handing back pass/fail, a tail of the log and
the path to the waveform.

The boundary matters more than the plumbing: the model chooses what to attempt,
WEFT chooses what may execute. There is no shell on the far end. The model
cannot invent a flag, cannot reach a path you have not opened to it, and cannot
run anything that is not on the list.

The other reason to wrap the tools is size. A Quartus compile leaves megabytes
of `.rpt` behind, and what you wanted from it was a resource line, an Fmax per
clock domain, and the two warnings that mattered. Tool results here are a few
kilobytes of JSON; the raw logs stay on disk and are fetched by name when
something actually needs them.

None of this designs anything. It will not write your RTL, close your timing, or
know which board is on your desk. It runs the commands you would have run, and
hands back something small enough to reason about.

## How it is put together

Quartus runs **on the host** — WEFT drives the installation you already have
and never tries to install or containerise it. Everything else WEFT executes
lives in one Podman image, `weft-tools`: Verilator, Icarus Verilog, GHDL and
Verible for HDL work, Tesseract and Poppler for reading documents. The
container runs with `--network=none` and sees nothing but your workspace.

Every path an MCP client supplies is resolved and checked against the
configured workspace root before it reaches the filesystem, on the host and
inside the container alike.

Nothing reaches the network at runtime, and nothing reports telemetry.

The full design — every tool's arguments and return shape, the milestones,
and the reasoning behind the awkward parts — is in [PROJECT.md](PROJECT.md).

## Requirements

- **Quartus Prime 25.1** (Lite, Standard or Pro) installed and licensed by you
- **Podman**, rootless
- **Python 3.11 or newer**
- `jtagd` for programming hardware, once that milestone lands

## Quick start

### Arch Linux

```bash
sudo pacman -S --needed podman python git

git clone https://github.com/FPGArtktic/weft-mcp.git
cd weft-mcp
podman build -t weft-tools -f containers/Containerfile.weft-tools .
pip install --user .
```

### Ubuntu 24.04 LTS

```bash
sudo apt update
sudo apt install podman uidmap python3 python3-pip git

git clone https://github.com/FPGArtktic/weft-mcp.git
cd weft-mcp
podman build -t weft-tools -f containers/Containerfile.weft-tools .
pip install --user .
```

`uidmap` is only a *Recommends* of `podman`, so a plain `apt install` pulls it
in but `--no-install-recommends` does not. Rootless Podman needs it.

**Ubuntu 22.04 ships Python 3.10**, which is below what WEFT needs. Either
move to 24.04 or install a newer interpreter, for instance with
[uv](https://github.com/astral-sh/uv):

```bash
uv venv --python 3.12 && uv pip install .
```

### Building the image

The image is **never distributed** — you build it, which keeps WEFT's own
distribution to GPL-3.0-only code and avoids shipping an aggregate of
third-party binaries under mixed licences. `podman build` is the only step
that needs network access; everything afterwards runs offline.

GHDL is compiled from source during the build, so expect it to take a while
the first time.

## Configuring

WEFT reads one TOML file, by default `~/.config/weft/weft.toml`:

```toml
[workspace]
# Nothing outside this directory can be read or written.
root = "/home/you/fpga"

[container]
image = "weft-tools"

[quartus]
edition = "lite"          # omit when only one edition is configured

[quartus.lite]
root = "/home/you/intelFPGA_lite/25.1std/quartus"

[quartus.pro]
root = "/opt/intelFPGA_pro/25.1/quartus"
# FlexLM variables are passed through to every Pro invocation.
env = { LM_LICENSE_FILE = "1800@licence-server" }

[jobs]
timeout_s = 7200

[rag]
# Where your PDFs live. Mounted read-only, and separate from the workspace
# because a document collection outlives any one project. Defaults to the
# workspace when omitted.
library = "/home/you/Documents/fpga-docs"

# Local BGE-M3 weights in ONNX form. Omit the key and document search still
# works, by text rather than by meaning.
model_path = "/home/you/.local/share/weft/models/bge-m3"

# The index itself. Defaults to <workspace>/.weft/documents.sqlite; put it
# somewhere shared to index a library once and use it from every project.
database = "/home/you/.local/share/weft/documents.sqlite"

[http]
host = "127.0.0.1"
port = 8080
token = "put-a-long-random-string-here"
```

Quartus paths always come from here. WEFT never guesses them and never
searches `PATH`. A machine with no Quartus simply omits the section — lint and
simulate do not need it.

Unknown keys are refused rather than ignored, so a typo fails at startup
instead of silently doing nothing.

## Running

Local client, over stdio:

```bash
weft --transport stdio
```

For Claude Desktop or Claude Code, register it as an MCP server:

```json
{
  "mcpServers": {
    "weft": {
      "command": "weft",
      "args": ["--transport", "stdio", "--config", "/home/you/.config/weft/weft.toml"]
    }
  }
}
```

On the LAN, over Streamable HTTP:

```bash
weft --transport http
```

Every request must carry `Authorization: Bearer <token>`; anything else gets a
401. Set a real token in the configuration — the HTTP transport refuses to
start without one.

### Why there is an HTTP transport at all

A local client does not need one; stdio is simpler and has no token to leak.
HTTP exists because it is the hand-off point for running this behind a model
you host yourself, on a network with no way out. Such a model, served behind
an OpenAI-compatible endpoint, talks to the same `/mcp` endpoint, and nothing
on the server side changes. WEFT already makes no network calls at runtime, so
an installed server needs nothing further.

Building that deployment — the inference cluster, the serving stack, carrying
the image and the wheels across the gap — is not part of this repository.
Appendix A of [PROJECT.md](PROJECT.md) records what it would take and stops
there, deliberately.

## Reading your own documents

Your PDFs go in the directory `[rag] library` points at — **not** in the
workspace. Standards and vendor handbooks are a personal library that outlives
any one project, and forcing a copy into every project tree would be absurd.
That directory is mounted **read-only**, so indexing cannot write anything into
your collection, and the index lands wherever `[rag] database` says. Point the
database at a shared path and one library serves every project.

`index_document` takes a PDF from there and makes it searchable. Pages with a text layer
are extracted directly; only pages that come back empty — a scan — are rendered
and passed to Tesseract, so a born-digital 1300-page standard is indexed in
seconds rather than an hour of pointless OCR.

The document is cut at its **own headings**, not into fixed-size windows, so a
result cites `1800-2017 §9.2.2.4` and you can look that up. A window number
could not be checked against anything.

Search is semantic when an embedding model is configured and textual when it is
not, and the result says which one answered — a ranking that came from a
substring match must not be mistaken for one that came from meaning. The
difference is real: against the SystemVerilog standard, "weighted random
selection" finds §18.16 *randcase* by meaning and finds nothing at all by text,
because those three words do not appear in it.

**No model ships with WEFT.** BGE-M3 is MIT-licensed and about 2 GB; you fetch
it once, point `model_path` at it, and nothing touches the network again:

```bash
DIR=~/.local/share/weft/models/bge-m3
mkdir -p $DIR/onnx
for f in onnx/model.onnx onnx/model.onnx_data tokenizer.json; do
    curl -Lo $DIR/$f https://huggingface.co/BAAI/bge-m3/resolve/main/$f
done
```

Point `model_path` at `$DIR`. The graph is small; `model.onnx_data` beside it
holds the weights and is the 2 GB.

Documents are yours and stay yours. WEFT ships no standards, no handbooks and
no vendor documentation, and the index it builds never leaves your workspace.

## Generated documentation

`generate_docs` writes a reference for a project and `generate_module_doc` for
a single module. Everything in them was computed by something: port and
parameter tables from the Verible and GHDL syntax trees, per-port descriptions
from the kernel-doc headers in your sources, the pin map from the fitter's own
`.pin` report, the resource and timing figures from the compilation reports.

What it does **not** do is describe what your module is for. A generator that
invents a sentence about a port produces a document that reads like a reference
and is not one, and you cannot tell which lines were computed and which were
guessed. An undocumented port gets a dash. Prose is the client model's job —
it can read the same facts and write them up, and then you know who said what.

[`examples/counter/docs/counter.md`](examples/counter/docs/counter.md) is
committed output, not a mock-up: 177 logic elements and 154.23 MHz from a real
compilation, and GitHub draws the hierarchy diagram.

Markdown gets the hierarchy as Mermaid, which GitHub and most Markdown viewers
render. HTML gets the same tree as inline SVG instead: a browser draws no
Mermaid, and a server that makes no network calls cannot hand it a renderer, so
an HTML page carrying Mermaid source would show the source. Generated documents
are indexed for retrieval as they are written, under `doc_type: "generated"`.

## The demo project

[`examples/counter/`](examples/counter/) is a small MAX 10 counter written in
SystemVerilog, Verilog-2001 and VHDL at once. The three languages are the
point: no open-source simulator reads more than one, so the project is a fair
test of whether a tool really handles a mixed hierarchy or only claims to.

## Contributing

Patches are welcome. WEFT follows the Linux kernel's habits: one logical
change per commit, `subsystem: summary` subjects, a body that explains *why*,
rebase rather than merge, and a `Signed-off-by:` line on everything. See
[CONTRIBUTING.md](CONTRIBUTING.md).

## Author

WEFT is written and maintained by **Mateusz Okulanis** —
[fpgartktic.github.io](https://fpgartktic.github.io/about/),
[@FPGArtktic](https://github.com/FPGArtktic), FPGArtktic@outlook.com.

Bug reports, patches and disagreements are all welcome — the last of those
especially, if you have driven this toolchain harder than I have.

## Licence

Copyright (C) 2026 Mateusz Okulanis.

GPL-3.0-only. The full text is in [COPYING](COPYING).

WEFT invokes Quartus and the containerised tools as separate programs and
distributes none of them.

## Trademarks

Intel, Altera and Quartus are trademarks of their respective owners. This
project is not affiliated with, endorsed by, or sponsored by Intel or Altera.
It contains no Intel or Altera code, files or documentation, and it neither
installs nor redistributes their software.
