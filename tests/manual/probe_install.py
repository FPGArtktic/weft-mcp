# SPDX-License-Identifier: GPL-2.0-only
"""Identify a real Quartus installation.

Run against a host that actually has Quartus; the unit tests deliberately do
not.

    python tests/manual/probe_install.py /path/to/quartus
"""

import sys
from pathlib import Path

from weft.quartus.install import InstallError, probe


def main(argv: list[str]) -> int:
    """main - print what WEFT makes of the installation at argv[1]."""
    if len(argv) != 2:
        print(__doc__)
        return 2

    try:
        install = probe(Path(argv[1]))
    except InstallError as e:
        print(f"failed: {e}")
        return 1

    print(f"root            {install.root}")
    print(f"edition         {install.edition}")
    print(f"version         {install.version}")
    print(f"build           {install.build}")
    print(f"synthesis       {install.synthesis_tool}")
    for tool in ("quartus_sh", "quartus_map", "quartus_fit", "quartus_asm", "quartus_sta"):
        print(f"  {tool:<14} {'found' if install.tool(tool).is_file() else 'MISSING'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
