# SPDX-License-Identifier: GPL-3.0-only
"""What an indexed design is made of.

One shape for every language. Verilog and VHDL spell these things
differently -- a port is a port either way, but its type reads
`logic [7:0]` in one and `std_logic_vector(7 downto 0)` in the other -- so
types are carried as the source wrote them rather than translated into some
neutral form nobody uses.
"""

from dataclasses import dataclass, field

VERILOG = "verilog"
SYSTEMVERILOG = "systemverilog"
VHDL = "vhdl"

IN = "in"
OUT = "out"
INOUT = "inout"


@dataclass(frozen=True)
class Port:
    """One port of a module or entity.

    @name: port name
    @direction: "in", "out" or "inout"
    @type: type as the source declares it, width and all
    """

    name: str
    direction: str
    type: str


@dataclass(frozen=True)
class Parameter:
    """One parameter or generic.

    @name: parameter name
    @default: default value as written, or None when it has none
    @type: declared type, or None when the language did not require one
    """

    name: str
    default: str | None = None
    type: str | None = None


@dataclass(frozen=True)
class Instance:
    """One instantiation inside a module.

    @name: instance label
    @of: name of the module or entity being instantiated
    @line: 1-based line of the instantiation
    """

    name: str
    of: str
    line: int


@dataclass(frozen=True)
class Module:
    """A module or entity, and what it declares.

    @name: module or entity name
    @language: "verilog", "systemverilog" or "vhdl"
    @file: path relative to the indexed directory
    @line: 1-based line where the declaration starts
    @ports / @parameters / @instances: in source order
    """

    name: str
    language: str
    file: str
    line: int
    ports: list[Port] = field(default_factory=list)
    parameters: list[Parameter] = field(default_factory=list)
    instances: list[Instance] = field(default_factory=list)
