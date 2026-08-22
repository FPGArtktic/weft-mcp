// SPDX-License-Identifier: GPL-2.0-only
/**
 * updown_counter - up/down counter with enable and synchronous clear
 * @WIDTH:  counter width in bits
 * @clk:    clock, rising edge active
 * @rst_n:  asynchronous reset, active low
 * @en:     count enable, one clock cycle per step
 * @up:     high counts up, low counts down
 * @clear:  synchronous clear, takes precedence over @en
 * @count:  current value
 *
 * The counter wraps in both directions; nothing signals the wrap.
 */
`timescale 1ns/1ps

module updown_counter #(
    parameter int WIDTH = 8
) (
    input  logic             clk,
    input  logic             rst_n,
    input  logic             en,
    input  logic             up,
    input  logic             clear,
    output logic [WIDTH-1:0] count
);

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n)     count <= '0;
        else if (clear) count <= '0;
        else if (en)    count <= up ? count + 1'b1 : count - 1'b1;
    end

endmodule
