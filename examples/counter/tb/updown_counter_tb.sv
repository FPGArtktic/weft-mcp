// SPDX-License-Identifier: GPL-3.0-only
/**
 * updown_counter_tb - checks counting, direction, enable and clear
 *
 * Pure SystemVerilog against a pure SystemVerilog unit, so Verilator and
 * Icarus can both run it. The full design cannot be simulated by either:
 * seven_seg_decoder is VHDL and no open simulator reads both languages in
 * one run.
 */
`timescale 1ns/1ps

module updown_counter_tb;

    localparam int WIDTH = 4;

    logic             clk = 1'b0;
    logic             rst_n = 1'b0;
    logic             en = 1'b0;
    logic             up = 1'b1;
    logic             clear = 1'b0;
    logic [WIDTH-1:0] count;

    int errors = 0;

    updown_counter #(.WIDTH(WIDTH)) dut (.*);

    always #5 clk = ~clk;

    task automatic check(input string what, input bit ok);
        if (ok) begin
            $display("[%0t] PASS: %s", $time, what);
        end else begin
            errors++;
            $display("[%0t] FAIL: %s", $time, what);
        end
    endtask

    task automatic step(input int n);
        repeat (n) @(posedge clk);
    endtask

    initial begin
        $dumpfile("updown_counter_tb.vcd");
        $dumpvars(0, updown_counter_tb);

        step(2);
        rst_n = 1'b1;
        @(posedge clk);
        check("reset clears the counter", count == '0);

        // Disabled: the counter must not move.
        step(5);
        check("count holds while disabled", count == '0);

        en = 1'b1;
        step(5);
        check("counts up when enabled", count == 4'd5);

        up = 1'b0;
        step(3);
        check("counts down when up is low", count == 4'd2);

        clear = 1'b1;
        @(posedge clk);
        clear = 1'b0;
        check("clear beats enable", count == '0);

        // Wrap: counting down from zero must land on the top value.
        step(1);
        check("wraps downwards", count == {WIDTH{1'b1}});

        if (errors == 0) $display("=== TEST PASSED ===");
        else             $display("=== TEST FAILED (%0d errors) ===", errors);
        $finish;
    end

    initial begin
        #1ms;
        $display("FAIL: simulation timed out");
        $finish;
    end

endmodule
