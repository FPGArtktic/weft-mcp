<!-- SPDX-License-Identifier: GPL-3.0-only -->

# Security policy

## Reporting a vulnerability

Report security problems privately, not as a public issue.

- Preferred: GitHub's private vulnerability reporting, from the **Security**
  tab of this repository.
- By email: **FPGArtktic@outlook.com**

Please include what you found, how to reproduce it, and what an attacker
could do with it. If you have a patch, sign it off as any other contribution.

Expect an acknowledgement within a week. WEFT is maintained by one person in
their own time, so please be patient with the fix itself; you will be told
where it stands.

## Supported versions

WEFT has not reached a stable release. Fixes land on the main branch, and
there are no backports.

## What is in scope

WEFT executes local toolchains on behalf of an LLM client, so the interesting
boundaries are:

- **The workspace sandbox.** Every client-supplied path must resolve inside
  the configured workspace root. A path, symlink or container mount that
  escapes it is a vulnerability, and so is anything that lets a tool write
  outside it.
- **The HTTP transport.** The bearer token guards every request. A way to
  reach a tool without presenting it is a vulnerability.
- **Container isolation.** `weft-tools` runs with `--network=none` and mounts
  only the workspace. Anything that widens that counts.
- **Command construction.** Client input becomes command lines. An injection
  that runs something WEFT did not intend counts.

## What is not in scope

- Anything a user can already do with their own shell. WEFT deliberately runs
  the tools you point it at.
- Vulnerabilities in Quartus, Verilator, GHDL, Verible, Tesseract or Podman
  themselves — report those upstream. Tell us anyway if WEFT's use of them
  makes one reachable that otherwise would not be.
- The image build step reaching the network. That is by design and documented;
  runtime is what must stay offline.
