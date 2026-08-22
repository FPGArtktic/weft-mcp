// SPDX-License-Identifier: GPL-3.0-only
/**
 * clk_tick - clock divider emitting a one-cycle tick at TICK_HZ
 * @CLK_HZ:  input clock frequency in Hz
 * @TICK_HZ: wanted tick frequency in Hz
 * @clk:     clock, rising edge active
 * @rst_n:   asynchronous reset, active low
 * @tick:    one cycle high every CLK_HZ / TICK_HZ cycles
 *
 * Deliberately plain Verilog-2001: no logic, no always_ff, no $clog2. The
 * rest of the design is SystemVerilog and VHDL, so this file is what proves
 * the toolchain copes with all three languages in one hierarchy.
 *
 * DIVISOR is declared as a vector so that WIDTH bits can be sliced out of it.
 * Verilog-2001 has no '() cast, and without the narrowing the comparison
 * would mix a WIDTH-bit counter with a 32-bit integer.
 */
`timescale 1ns/1ps

module clk_tick #(
    parameter CLK_HZ  = 50000000,
    parameter TICK_HZ = 2
) (
    input  wire clk,
    input  wire rst_n,
    output reg  tick
);

    // ceil(log2(value)) - the constant-function stand-in for $clog2.
    function integer clogb2;
        input integer value;
        integer i;
        begin
            clogb2 = 0;
            for (i = value - 1; i > 0; i = i >> 1)
                clogb2 = clogb2 + 1;
        end
    endfunction

    localparam [31:0] DIVISOR = CLK_HZ / TICK_HZ;
    localparam        WIDTH   = (DIVISOR <= 2) ? 1 : clogb2(DIVISOR);

    localparam [WIDTH-1:0] LAST = DIVISOR[WIDTH-1:0] - 1'b1;

    reg [WIDTH-1:0] cnt;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            cnt  <= {WIDTH{1'b0}};
            tick <= 1'b0;
        end else if (cnt == LAST) begin
            cnt  <= {WIDTH{1'b0}};
            tick <= 1'b1;
        end else begin
            cnt  <= cnt + 1'b1;
            tick <= 1'b0;
        end
    end

endmodule
