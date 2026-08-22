<!-- SPDX-License-Identifier: GPL-3.0-only -->

# WEFT

```{rubric} WEFT Elaborates FPGA Toolchains
```

An MCP server that gives an LLM client a structured interface to an Intel
Quartus Prime FPGA flow: lint and simulate in seconds, compile asynchronously,
search your own standards, and read results back as JSON instead of megabytes
of report.

Quartus and Questa run **on the host**, from the installations you already
have. Everything else — Verilator, Icarus, GHDL, Verible, Tesseract — runs in
one Podman container with no network and nothing mounted but your workspace.

:::{admonition} Nothing reaches the network at runtime
:class: important

After installation WEFT makes no outbound request and reports no telemetry.
The container runs with `--network=none`. The one step that needs the network
is building the image.
:::

::::{grid} 1 1 2 2
:gutter: 3

:::{grid-item-card} {octicon}`rocket` Install it
:link: installation
:link-type: doc

Arch and Ubuntu quick starts, what you have to supply yourself, and how to
register the server with a client.
:::

:::{grid-item-card} {octicon}`gear` Configure it
:link: configuration
:link-type: doc

Every key of `weft.toml`, what it means and what it defaults to — read out of
the loader itself, so the list cannot drift.
:::

:::{grid-item-card} {octicon}`tools` The tools
:link: tools
:link-type: doc

All twenty, as an MCP client sees them, with their arguments. Generated from
the running server.
:::

:::{grid-item-card} {octicon}`book` The design
:link: specification
:link-type: doc

Why the awkward parts are the way they are: persistent jobs, AST indexing,
facts versus prose in generated documents.
:::

::::

## What it actually does

::::{grid} 1 1 3 3
:gutter: 2

:::{grid-item-card} Fast loop
Lint and simulate without touching Quartus. Verilator, Icarus and GHDL in the
container; host Questa when a design mixes Verilog and VHDL, because no open
simulator reads both in one run.
:::

:::{grid-item-card} Compilation
Quartus runs as a persistent job. Kill the server mid-compile, restart it, and
the job's status is still correct. Reports come back as a few kilobytes of
JSON rather than megabytes of `.rpt`.
:::

:::{grid-item-card} Retrieval
Your own PDFs, OCR'd only where a page has no text layer, cut at their own
clause headings so a result cites `1800-2017 §9.2.2.4` rather than a page
number nobody can check.
:::

::::

## The division of labour

WEFT supplies facts. It does not describe what your module is for, and it does
not write your RTL. Generated documentation is tables, pin maps and hierarchy
diagrams computed from syntax trees and reports; an undocumented port gets a
dash, not a sentence about what the generator imagines it does.

That boundary is the point. Everything the model repeats back to you came from
a tool you can run yourself and check.

```{toctree}
:hidden:
:maxdepth: 2

installation
configuration
tools
specification
changelog
```

## Elsewhere

- The [repository](https://github.com/FPGArtktic/weft-mcp), and its
  [README](https://github.com/FPGArtktic/weft-mcp#what-this-is-for-if-mcp-is-new-to-you),
  which explains what a tool server is for if MCP is new to you.
- [`examples/counter/`](https://github.com/FPGArtktic/weft-mcp/tree/main/examples/counter)
  — a MAX 10 demonstration written in SystemVerilog, Verilog-2001 and VHDL at
  once, with its generated documentation committed beside it.
