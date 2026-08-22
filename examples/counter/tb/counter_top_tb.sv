// SPDX-License-Identifier: GPL-2.0-only
/**
 * counter_top_tb - drives the whole design through its button sequences
 *
 * The unit's parameters are scaled right down so a full run takes a fraction
 * of a second.
 *
 * This testbench needs a mixed-language simulator such as Questa: the design
 * instantiates a VHDL entity, and neither Verilator nor Icarus reads VHDL.
 * The per-module testbenches beside it run under the open simulators.
 */
`timescale 1ns/1ps

module counter_top_tb;

    localparam int  CLK_HZ     = 1_000_000;   // 1 MHz, so one period is 1 us
    localparam int  TICK_HZ    = 100_000;     // a tick every ten cycles
    localparam int  STABLE_MS  = 1;           // 1000 cycles of stability
    localparam time CLK_PERIOD = 1us;

    // How long a button must be held for the debouncer to accept it.
    localparam int  DEB_CYCLES  = (CLK_HZ / 1000) * STABLE_MS;
    localparam int  TICK_CYCLES = CLK_HZ / TICK_HZ;

    logic       clk = 1'b0;
    logic       rst_n = 1'b0;
    logic [1:0] btn_n = 2'b11;
    logic [7:0] led;
    logic [6:0] seg_n;
    logic       heartbeat;

    logic [7:0] snapshot;
    int         errors = 0;

    counter_top #(
        .CLK_HZ    (CLK_HZ),
        .TICK_HZ   (TICK_HZ),
        .STABLE_MS (STABLE_MS)
    ) dut (.*);

    always #(CLK_PERIOD/2) clk = ~clk;

    task automatic wait_cycles(input int n);
        repeat (n) @(posedge clk);
    endtask

    // Press and release a button; both edges are active low.
    task automatic press(input int idx);
        btn_n[idx] = 1'b0;
        wait_cycles(2 * DEB_CYCLES);
        btn_n[idx] = 1'b1;
        wait_cycles(2 * DEB_CYCLES);
    endtask

    task automatic check(input string what, input bit ok);
        if (ok) begin
            $display("[%0t] PASS: %s", $time, what);
        end else begin
            errors++;
            $display("[%0t] FAIL: %s", $time, what);
        end
    endtask

    initial begin
        wait_cycles(5);
        rst_n = 1'b1;
        @(posedge clk);
        check("counter clear after reset", led == 8'd0);

        // Counting up is the state reset leaves behind.
        wait_cycles(5 * TICK_CYCLES);
        check("counter counted up", led inside {[8'd4 : 8'd6]});

        // btn_n[1] pauses.
        press(1);
        snapshot = led;
        wait_cycles(20 * TICK_CYCLES);
        check("pause stops the counter", led == snapshot);
        check("display blanks while paused", seg_n == 7'b111_1111);

        // Resume, then turn around.
        press(1);
        press(0);
        snapshot = led;
        wait_cycles(5 * TICK_CYCLES);
        // Compared modulo 256, since the counter may pass through zero.
        check("counter counted down", 8'(snapshot - led) inside {[8'd4 : 8'd6]});

        // Both buttons together clear the counter.
        btn_n = 2'b00;
        wait_cycles(4 * DEB_CYCLES);
        check("both buttons clear the counter", led == 8'd0);
        btn_n = 2'b11;

        if (errors == 0) $display("=== TEST PASSED ===");
        else             $display("=== TEST FAILED (%0d errors) ===", errors);
        $finish;
    end

    // Guard against a hung simulation.
    initial begin
        #100ms;
        $display("FAIL: simulation timed out");
        $finish;
    end

endmodule
