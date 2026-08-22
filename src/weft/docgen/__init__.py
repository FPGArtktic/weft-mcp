# SPDX-License-Identifier: GPL-3.0-only
"""Generating documentation from what the tools already know.

Facts only. Ports and hierarchy come from the AST, the pin map from the
fitter, timing from the timing analyser, prose from the header the author
wrote. Nothing here describes what a module is for, because nothing here
knows: that is the client model's job, and a plausible invention in a
document that reads like a reference is worse than a gap.
"""
