// SPDX-License-Identifier: GPL-3.0-only
/**
 * debouncer - input synchroniser, contact debouncer and edge detector
 * @CLK_HZ:     clock frequency in Hz, used to size the stability counter
 * @STABLE_MS:  how long the input must hold its new value to be accepted
 * @clk:        clock, rising edge active
 * @rst_n:      asynchronous reset, active low
 * @noisy_in:   raw input, asynchronous to @clk
 * @clean_out:  debounced level
 * @rise_pulse: one cycle high on every 0 -> 1 edge of @clean_out
 *
 * Two flip-flops bring @noisy_in into the clock domain first; only then does
 * the stability counter run, so metastability never reaches it. Any bounce
 * restarts the count, which is why a burst of chatter costs one @STABLE_MS
 * window rather than one per edge.
 */
`timescale 1ns/1ps

module debouncer #(
    parameter int CLK_HZ    = 50_000_000,
    parameter int STABLE_MS = 10
) (
    input  logic clk,
    input  logic rst_n,
    input  logic noisy_in,
    output logic clean_out,
    output logic rise_pulse
);

    localparam int LIMIT = (CLK_HZ / 1000) * STABLE_MS;
    localparam int WIDTH = (LIMIT <= 2) ? 1 : $clog2(LIMIT);

    logic [1:0]       sync;
    logic [WIDTH-1:0] cnt;
    logic             clean_q;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) sync <= '0;
        else        sync <= {sync[0], noisy_in};
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            cnt       <= '0;
            clean_out <= 1'b0;
        end else if (sync[1] == clean_out) begin
            cnt <= '0;                       // no change, restart the window
        end else if (cnt == WIDTH'(LIMIT - 1)) begin
            cnt       <= '0;
            clean_out <= sync[1];            // the change held, accept it
        end else begin
            cnt <= cnt + 1'b1;
        end
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) clean_q <= 1'b0;
        else        clean_q <= clean_out;
    end

    assign rise_pulse = clean_out & ~clean_q;

endmodule
