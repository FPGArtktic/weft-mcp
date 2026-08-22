# SPDX-License-Identifier: GPL-2.0-only
"""Create a project with a real Quartus and compile it.

The unit tests capture the Tcl instead of running it; this checks that Quartus
actually accepts what WEFT generates.

    python tests/manual/make_project.py /path/to/quartus /tmp/scratch
"""

import subprocess
import sys
from pathlib import Path

from weft.quartus.install import probe
from weft.quartus.project import Assignment, create_project, set_assignments

SOURCE = "module tiny (input wire a, output wire y);\n    assign y = ~a;\nendmodule\n"


def main(argv: list[str]) -> int:
    """main - build a one-module project and run a full compile over it."""
    if len(argv) != 3:
        print(__doc__)
        return 2

    install = probe(Path(argv[1]))
    directory = Path(argv[2]) / "weft-make-project"

    create_project(
        install, directory, "demo", "MAX 10", "10M04SAE144A7G", top="tiny", overwrite=True
    )
    (directory / "src").mkdir(exist_ok=True)
    (directory / "src" / "tiny.v").write_text(SOURCE)

    qsf = set_assignments(
        install,
        directory,
        "demo",
        [
            Assignment("global", name="VERILOG_FILE", value="src/tiny.v"),
            Assignment("global", name="PROJECT_OUTPUT_DIRECTORY", value="output_files"),
            Assignment("location", value="PIN_27", to="a"),
            Assignment("instance", name="IO_STANDARD", value="3.3-V LVTTL", to="a"),
        ],
    )
    print(f"wrote {qsf}")

    done = subprocess.run(
        [str(install.require("quartus_sh")), "--flow", "compile", "demo"],
        cwd=str(directory),
        capture_output=True,
        text=True,
        check=False,
    )
    print(f"compile exit {done.returncode}")
    for line in done.stdout.splitlines():
        if "successful" in line or line.startswith("Error"):
            print(f"  {line}")

    reports = sorted(p.name for p in (directory / "output_files").glob("*"))
    print(f"reports: {', '.join(reports) or 'none'}")
    return 0 if done.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
