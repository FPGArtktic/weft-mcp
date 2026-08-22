<!-- SPDX-License-Identifier: GPL-3.0-only -->

# Contributing to WEFT

Patches are welcome. WEFT borrows the Linux kernel's working habits, so a few
things differ from the usual GitHub flow. None of it is ceremony: each rule
exists so that the history stays readable years from now.

## Sign your work

Every commit carries a `Signed-off-by:` line. It certifies that you wrote the
patch or otherwise have the right to submit it under GPL-3.0-only, in the
sense of the [Developer Certificate of Origin](https://developercertificate.org/).

```bash
git commit -s
```

A patch without a sign-off cannot be merged, however good it is.

## One logical change per commit

A commit does one thing. If you cannot describe it in a single sentence
without "and", it is two commits. Fixing a typo you introduced two commits
earlier is not a commit — rebase it into the one that introduced it.

## Commit messages

```
subsystem: summary in the imperative, under 72 characters

The body explains why, wrapped at 72 columns. The diff already shows
what changed; what it cannot show is the reason, the alternative you
rejected, or the failure that made the change necessary.

Signed-off-by: Your Name <you@example.com>
```

The subsystem prefix is lowercase and matches the tree: `server`, `jobs`,
`quartus`, `fastloop`, `index`, `rag`, `docs`, `containers`, `ci`, or
`Documentation`.

## Rebase, never merge

The main branch is linear. Rebase your work onto it before opening a pull
request, and rebase again rather than merging when it falls behind. No merge
commits.

## Code

Python is PEP 8 as enforced by `ruff`, plus the kernel's taste on top: short
functions, early returns, flat control flow, no speculative abstraction. When
a special case appears, look for the way to make it disappear rather than
branching around it. Handle errors where they happen; never swallow one
silently.

Every file starts with `# SPDX-License-Identifier: GPL-3.0-only`, in whatever
comment syntax the language uses. Public functions carry a kernel-doc-flavoured
docstring: one-line summary, arguments, return value, failure modes.

HDL in `examples/` follows the header convention in `PROJECT.md` §8.3.

All comments, documentation and commit messages are in English.

## Tests

```bash
ruff check . && ruff format --check .
pytest
```

Tests must pass without Quartus and without a board: mock the subprocess
boundary. Tests that genuinely need the container are marked `container` and
skip themselves when Podman or the `weft-tools` image is absent.

When you fix a bug, add the test that would have caught it. When you parse a
tool's output, capture that output verbatim into the test, so that a change in
the tool's format fails here rather than in front of a user.

## Dependencies

Prefer the standard library. A new dependency needs a one-line justification
in the commit body and a check that its licence is compatible with
GPL-3.0-only. Apache-2.0 is compatible in that direction, so an Apache-2.0
dependency may be used; GPLv2-only code may not, because this project moved
past it.

## Scope

WEFT drives a Quartus flow through MCP. It is not a web UI, not a Vivado
front end, and not a cloud service. Patches that widen the scope will be asked
to narrow it first.
