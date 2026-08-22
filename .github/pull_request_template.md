<!-- SPDX-License-Identifier: GPL-3.0-only -->

## What this changes, and why

<!--
The diff shows what. Explain why: the failure you hit, the alternative you
rejected, the reason this is the right shape. Wrap at a sensible width.
-->

## Checklist

<!-- See CONTRIBUTING.md. These are habits, not paperwork. -->

- [ ] Every commit is signed off (`git commit -s`)
- [ ] One logical change per commit; no "fix typo in previous commit"
- [ ] Subjects read `subsystem: summary`, imperative, under 72 characters
- [ ] Rebased on the main branch, no merge commits
- [ ] `ruff check .` and `ruff format --check .` are clean
- [ ] `pytest` passes without Quartus and without a board
- [ ] New behaviour has a test; a bug fix has the test that would have caught it
- [ ] Any new dependency is justified in the commit body and is GPL-3.0-only compatible
