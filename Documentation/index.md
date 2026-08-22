<!-- SPDX-License-Identifier: GPL-3.0-only -->

# WEFT

An MCP server that gives an LLM client a structured interface to an Intel
Quartus Prime FPGA flow: lint and simulate in seconds, compile asynchronously,
search your own standards, and read results back as JSON instead of megabytes
of report.

Quartus and Questa run on the **host**, from the installations you already
have. Everything else WEFT executes — Verilator, Icarus, GHDL, Verible,
Tesseract — runs in one Podman container with no network and nothing mounted
but your workspace. Nothing reaches the network at runtime and nothing reports
telemetry.

```{toctree}
:maxdepth: 2

installation
configuration
tools
specification
changelog
```

## Where to start

If MCP is new to you, the [README](https://github.com/FPGArtktic/weft-mcp#what-this-is-for-if-mcp-is-new-to-you)
explains what a tool server is for before this manual explains how to run one.

The [tool reference](tools.md) and the [configuration reference](configuration.md)
are generated from the code on every build, so neither can drift from what the
server actually accepts.
