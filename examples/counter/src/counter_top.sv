// SPDX-License-Identifier: GPL-2.0-only
/**
 * counter_top - eight-bit demonstration counter driving LEDs and a display
 * @CLK_HZ:    board clock frequency in Hz
 * @TICK_HZ:   how many steps per second the counter takes
 * @STABLE_MS: debounce window for the push buttons
 * @clk:       board clock, rising edge active
 * @rst_n:     asynchronous reset, active low
 * @btn_n:     two push buttons, active low: [0] direction, [1] run/pause
 * @led:       the counter value
 * @seg_n:     low nibble of the counter on a seven-segment display
 * @heartbeat: 1 Hz blink, evidence that the clock is alive
 *
 * Holding both buttons at once clears the counter. The display blanks while
 * the counter is paused, so a stopped count cannot be mistaken for a slow one.
 *
 * The hierarchy spans three languages on purpose: this module and the
 * debouncer and counter are SystemVerilog, clk_tick is Verilog-2001, and
 * seven_seg_decoder is VHDL. Any tool that claims to index or document this
 * project has to cross those boundaries.
 */
`timescale 1ns/1ps

module counter_top #(
    parameter int CLK_HZ    = 50_000_000,
    parameter int TICK_HZ   = 4,
    parameter int STABLE_MS = 10
) (
    input  logic       clk,
    input  logic       rst_n,
    input  logic [1:0] btn_n,
    output logic [7:0] led,
    output logic [6:0] seg_n,
    output logic       heartbeat
);

    // ------------------------------------------------------------------ reset
    // Reset is released asynchronously, so the release edge is synchronised
    // into the clock domain before anything downstream sees it.
    logic [1:0] rst_sync;
    logic       rst_n_s;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) rst_sync <= 2'b00;
        else        rst_sync <= {rst_sync[0], 1'b1};
    end

    assign rst_n_s = rst_sync[1];

    // ---------------------------------------------------------------- buttons
    logic btn_dir, btn_run;
    logic dir_pulse, run_pulse;

    debouncer #(
        .CLK_HZ    (CLK_HZ),
        .STABLE_MS (STABLE_MS)
    ) u_btn_dir (
        .clk        (clk),
        .rst_n      (rst_n_s),
        .noisy_in   (~btn_n[0]),
        .clean_out  (btn_dir),
        .rise_pulse (dir_pulse)
    );

    debouncer #(
        .CLK_HZ    (CLK_HZ),
        .STABLE_MS (STABLE_MS)
    ) u_btn_run (
        .clk        (clk),
        .rst_n      (rst_n_s),
        .noisy_in   (~btn_n[1]),
        .clean_out  (btn_run),
        .rise_pulse (run_pulse)
    );

    logic clear_req;
    assign clear_req = btn_dir & btn_run;

    // ---------------------------------------------------------------- control
    logic dir_up;
    logic running;

    always_ff @(posedge clk or negedge rst_n_s) begin
        if (!rst_n_s) begin
            dir_up  <= 1'b1;
            running <= 1'b1;
        end else if (!clear_req) begin
            // While both buttons are down the user is clearing, not steering.
            if (dir_pulse) dir_up  <= ~dir_up;
            if (run_pulse) running <= ~running;
        end
    end

    // --------------------------------------------------------------- counting
    logic tick;

    clk_tick #(
        .CLK_HZ  (CLK_HZ),
        .TICK_HZ (TICK_HZ)
    ) u_tick (
        .clk   (clk),
        .rst_n (rst_n_s),
        .tick  (tick)
    );

    logic [7:0] count;

    updown_counter #(
        .WIDTH (8)
    ) u_count (
        .clk   (clk),
        .rst_n (rst_n_s),
        .en    (tick & running),
        .up    (dir_up),
        .clear (clear_req),
        .count (count)
    );

    // -------------------------------------------------------------- heartbeat
    logic hb_tick;

    clk_tick #(
        .CLK_HZ  (CLK_HZ),
        .TICK_HZ (2)              // two ticks a second, halved into a 1 Hz blink
    ) u_hb (
        .clk   (clk),
        .rst_n (rst_n_s),
        .tick  (hb_tick)
    );

    always_ff @(posedge clk or negedge rst_n_s) begin
        if (!rst_n_s)     heartbeat <= 1'b0;
        else if (hb_tick) heartbeat <= ~heartbeat;
    end

    // ---------------------------------------------------------------- outputs
    seven_seg_decoder u_seg (
        .nibble (count[3:0]),
        .blank  (~running),
        .seg_n  (seg_n)
    );

    assign led = count;

endmodule
