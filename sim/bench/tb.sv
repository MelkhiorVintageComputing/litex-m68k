// SPDX-License-Identifier: BSD-2-Clause
// Copyright 2026 Romain Dolbeau

// L1 bench harness: the clock, the reset, the console and the verdict around
// bench.v (bench.py).
//
// Plusargs:
//   +ram=<file>      the RAM image, $readmemh, one 32-bit word per line (required)
//   +expect=<hex>    the value the program must write to RESULT
//   +max_cycles=<n>  give up after n clocks (default 50,000,000)
//   +vcd=<file>      dump a VCD
//
// The verdict is the last line: "PASS", or "FAIL: <why>". Exit status 0 for PASS.

`timescale 1ns/1ps

module tb;
    logic        clk = 1'b0;
    logic        rst = 1'b1;
    logic [7:0]  tty_data;
    logic        tty_stb, exit_stb, result_stb;
    logic [31:0] exit_code, result;

    bench dut (
        .sys_clk(clk), .sys_rst(rst),
        .tty_stb(tty_stb), .tty_data(tty_data),
        .exit_stb(exit_stb), .exit_code(exit_code),
        .result_stb(result_stb), .result(result)
    );

    // RD68031 needs a 50 % duty cycle: both edges are bus states.
    always #5 clk = ~clk;

    string       ram_file, vcd_file;
    logic [31:0] expected;
    logic        have_expected, seen_result;
    longint      max_cycles, cycles;

    initial begin
        if (!$value$plusargs("ram=%s", ram_file)) begin
            $display("FAIL: no +ram=");
            $fatal(1);
        end
        $readmemh(ram_file, dut.ram);
        have_expected = $value$plusargs("expect=%h", expected);
        if (!$value$plusargs("max_cycles=%d", max_cycles))
            max_cycles = 50_000_000;
        if ($value$plusargs("vcd=%s", vcd_file)) begin
            $dumpfile(vcd_file);
            $dumpvars(0, tb);
        end
        seen_result = 1'b0;
        repeat (8) @(posedge clk);
        rst = 1'b0;
    end

    always @(posedge clk) begin
        cycles <= rst ? 0 : cycles + 1;
        if (!rst && cycles >= max_cycles) begin
            $display("\nFAIL: no EXIT after %0d clocks", cycles);
            $fatal(1);
        end
        if (tty_stb)
            $write("%c", tty_data);
        if (result_stb) begin
            $display("RESULT %08x", result);
            if (have_expected && result !== expected) begin
                $display("FAIL: RESULT %08x, expected %08x", result, expected);
                $fatal(1);
            end
            seen_result = 1'b1;
        end
        if (exit_stb) begin
            $display("EXIT %08x after %0d clocks", exit_code, cycles);
            if (exit_code !== 32'd0) begin
                $display("FAIL: exit status %08x", exit_code);
                $fatal(1);
            end
            if (have_expected && !seen_result) begin
                $display("FAIL: no RESULT");
                $fatal(1);
            end
            $display("PASS");
            $finish;
        end
    end
endmodule
