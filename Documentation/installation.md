<!-- SPDX-License-Identifier: GPL-3.0-only -->

# Installation

## What you supply

WEFT installs none of these and redistributes none of them.

| | |
|---|---|
| Quartus Prime 25.1 | Lite, Standard or Pro, installed and licensed by you |
| Questa - Altera Starter FPGA Edition | optional; ships beside Quartus |
| Podman | rootless |
| Python | 3.11 or newer |
| `jtagd` | only for programming hardware |

## Arch Linux

```bash
sudo pacman -S --needed podman python git

git clone https://github.com/FPGArtktic/weft-mcp.git
cd weft-mcp
podman build -t weft-tools -f containers/Containerfile.weft-tools .
pip install --user .
```

## Ubuntu 24.04 LTS

```bash
sudo apt update
sudo apt install podman uidmap python3 python3-pip git

git clone https://github.com/FPGArtktic/weft-mcp.git
cd weft-mcp
podman build -t weft-tools -f containers/Containerfile.weft-tools .
pip install --user .
```

:::{warning}
`uidmap` is only a *Recommends* of `podman`, so a plain `apt install` pulls it
in but `--no-install-recommends` does not. Rootless Podman needs it.

Ubuntu 22.04 ships Python 3.10, which is below what WEFT needs. Move to 24.04
or install a newer interpreter, for instance with
[uv](https://github.com/astral-sh/uv).
:::

## The container image

The image is **never distributed** — you build it. That keeps WEFT's own
distribution to GPL-3.0-only code rather than an aggregate of third-party
binaries under mixed licences. `podman build` is the only step that needs
network access; everything afterwards runs offline.

:::{note}
GHDL is compiled from source during the build, so the first one takes a while.
It is also the only step in this whole document that touches the network.
:::

## Running

```bash
weft --transport stdio          # a local client
weft --transport http           # on the LAN, behind a bearer token
```

Registering it with an MCP client:

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
