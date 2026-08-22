<!-- SPDX-License-Identifier: GPL-3.0-only -->

# counter — the WEFT demonstration project

An eight-bit up/down counter for a MAX 10 device (`10M04SAE144A7G`), written
deliberately in **three languages at once**. It exists to exercise WEFT
against a design that is small enough to read in one sitting and awkward
enough to be worth testing.

## What it does

| Signal | Direction | Meaning |
|---|---|---|
| `clk` | in | board clock, 50 MHz by default |
| `rst_n` | in | asynchronous reset, active low |
| `btn_n[0]` | in | change direction, up or down |
| `btn_n[1]` | in | run or pause |
| `led[7:0]` | out | the counter value |
| `seg_n[6:0]` | out | low nibble on a seven-segment display, common anode |
| `heartbeat` | out | 1 Hz blink, evidence the clock is alive |

Holding both buttons at once clears the counter. The display blanks while
paused, so a stopped count cannot be mistaken for a slow one.

## Structure

```
src/
  counter_top.sv         [SystemVerilog] top level: reset sync, control, wiring
  debouncer.sv           [SystemVerilog] synchroniser, debouncer, edge detector
  updown_counter.sv      [SystemVerilog] the counter itself
  clk_tick.v             [Verilog-2001]  clock divider producing a one-cycle tick
  seven_seg_decoder.vhd  [VHDL]          hex digit to seven-segment patterns
tb/
  updown_counter_tb.sv   [SystemVerilog] counter checks, runs under Verilator and Icarus
  seven_seg_decoder_tb.vhd [VHDL]        decoder checks, runs under GHDL
  counter_top_tb.sv      [SystemVerilog] whole design, needs a mixed-language simulator
counter.qsf              assignments
counter.sdc              timing constraints
```

The hierarchy is `counter_top` → {`debouncer` ×2, `clk_tick` ×2,
`updown_counter`, `seven_seg_decoder`}, and it crosses a language boundary
twice. Anything that claims to index, lint or document this project has to
handle all three parsers and stitch the results back together.

## Building

```bash
quartus_sh --flow compile counter
```

No pin locations are assigned: they belong to a particular board and this
example does not assume one. Quartus places the I/O itself and writes the
result to `output_files/counter.pin`. Add the locations your board needs
before programming real hardware.

## Simulating

**No open-source simulator can run the whole design.** Verilator and Icarus do
not read VHDL, GHDL does not read Verilog, and this design contains both. That
is a property of the tools, not a defect in the project — and it is one of the
reasons this example is shaped the way it is.

So each language is exercised by its own testbench:

```bash
# the counter, under Verilator
verilator --binary --timing -Isrc src/updown_counter.sv tb/updown_counter_tb.sv \
    --top-module updown_counter_tb

# the decoder, under GHDL
ghdl -a --std=08 src/seven_seg_decoder.vhd tb/seven_seg_decoder_tb.vhd
ghdl -r --std=08 seven_seg_decoder_tb
```

`tb/counter_top_tb.sv` drives the complete design and needs a mixed-language
simulator such as Questa:

```bash
vlib work
vcom -2008 src/seven_seg_decoder.vhd
vlog -sv src/counter_top.sv src/debouncer.sv src/updown_counter.sv src/clk_tick.v
vlog -sv tb/counter_top_tb.sv
vsim -c counter_top_tb -do "run -all; quit"
```

## Through WEFT

Point the workspace root at this directory and the tools take the same paths:

```jsonc
lint     { "files": ["src/updown_counter.sv"], "language": "verilog" }
lint     { "files": ["src/seven_seg_decoder.vhd"], "language": "vhdl" }
simulate { "files": ["src/seven_seg_decoder.vhd"], "top": "seven_seg_decoder_tb",
           "testbench": "tb/seven_seg_decoder_tb.vhd" }
```

`simulate` accepts the whole source list even though it spans both languages:
the testbench decides which language runs, and the files left out are named in
the result.

One thing to expect from `lint`: linting `counter_top.sv` with Verilator
reports `MODMISSING` for `seven_seg_decoder`. The module is not missing — it is
written in VHDL, and Verilator cannot see it. Linting each module on its own is
clean, and so is linting the whole thing through Questa, which reads both
languages:

```jsonc
lint { "files": ["src/counter_top.sv", "src/debouncer.sv", "src/updown_counter.sv",
                 "src/clk_tick.v", "src/seven_seg_decoder.vhd"],
       "linter": "questa" }
```

That returns no diagnostics and an empty `excluded`. The same file list given
to Verilator returns the false `MODMISSING` and names the VHDL it had to drop.

## The `docs/` directory

Everything in `docs/` is committed **generated output**, not hand-written
documentation. It is exactly what an MCP client gets from these calls against
this project, with the workspace root one level up:

```jsonc
index_project           { "directory": "counter" }
generate_docs           { "project_ref": "counter" }
generate_compilation_doc{ "project_ref": "counter" }
generate_module_doc     { "module": "counter_top", "project_ref": "counter" }
// ... once per module
```

That is a project reference, a compilation summary, and a page for each of the
eight modules, testbenches included.

`counter-compilation.md` is the one to read after a build. It opens with the
three things worth knowing before any table — did it build, did timing close,
what needs attention — and keeps the two critical warnings apart from the four
routine ones, because Quartus prints the routine ones every single time and
burying the real ones among them is how they get missed. It names the check
each slack belongs to, too: the smallest number here is 0.144 ns of *hold*
slack, and a reader shown that bare would take it for setup and conclude the
design is out of speed when it runs at 154 MHz.

Every number in them was computed. The port tables and the hierarchy come from
the Verible and GHDL parse trees; the per-port descriptions from the kernel-doc
headers in the sources; the pin map from the fitter's `.pin` report; the 177
logic elements and 154.23 MHz Fmax from a real Quartus 25.1 compilation of this
project. Nothing in them is prose about what a module is *for* — that is the
client model's job, and the generator does not guess. A port whose header line
is missing gets a dash.

Paths inside them, such as `counter/src/counter_top.sv:24`, are relative to the
workspace root rather than to this directory, because that is what every WEFT
tool takes and returns. Hand one back to `get_module_info` and it works.

They carry no timestamp, so regenerating produces no diff unless the design
actually changed. `generate_docs` also writes HTML, with the hierarchy as
inline SVG rather than Mermaid — a browser draws no Mermaid and an offline
server may not fetch a renderer — but that output is not committed here.
