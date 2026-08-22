<!-- SPDX-License-Identifier: GPL-2.0-only -->

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
| Quartus compilation, jobs, `parse_reports` | not yet |
| Source indexing, `search_code`, `get_hierarchy` | not yet |
| Document RAG with OCR | not yet |
| Documentation generation | not yet |
| Device programming | not yet |

Both transports work: stdio for a local client, Streamable HTTP behind a
static bearer token for a client on the LAN.

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
distribution to GPL-2.0-only code and avoids shipping an aggregate of
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

GPL-2.0-only. The full text is in [COPYING](COPYING).

WEFT invokes Quartus and the containerised tools as separate programs and
distributes none of them.

## Trademarks

Intel, Altera and Quartus are trademarks of their respective owners. This
project is not affiliated with, endorsed by, or sponsored by Intel or Altera.
It contains no Intel or Altera code, files or documentation, and it neither
installs nor redistributes their software.
