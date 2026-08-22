# SPDX-License-Identifier: GPL-3.0-only
"""WEFT - an MCP server for the Intel Quartus Prime flow."""

#: The one place the version is written. pyproject reads it from here, so
#: a release cannot bump the package and leave the server announcing the
#: version before it -- which is exactly what happened for 0.4.0.
__version__ = "0.5.0"
