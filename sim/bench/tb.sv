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
//   +cpu_half=<ns>   with FIFO_BRIDGE defined (bench.py --bridge fifo): half the
//                    period of the processor's clock (default 17.3; sys: 5)
//   +trace           with RD68041 defined (bench.py --cpu rd68041): every
//                    transfer's start, and every long word's TA, on the console
//
// With SMP defined (bench.py --smp, L19), RD68041's m14_smp.S on two
// processors: the run ends when both have written DONE, and memory and the
// board's counters are checked then, as RD68041's core_smp_tb checks them.
//   +iters=<n>       the ITERS the image was made with (smp_image.py; default 6)
//   +gapless         the negative run: PASS only if the printed arbiter's
//                    handover with no clock between is seen
//
// The verdict is the last line: "PASS", or "FAIL: <why>". Exit status 0 for PASS.

`timescale 1ns/1ps

module tb;
    logic        clk = 1'b0;
    logic        rst = 1'b1;
    logic [7:0]  tty_data;
    logic        tty_stb, exit_stb, result_stb;
    logic [31:0] exit_code, result;

`ifdef FIFO_BRIDGE
    logic        cpu_clk = 1'b0;
    real         cpu_half;
    initial if (!$value$plusargs("cpu_half=%f", cpu_half)) cpu_half = 17.3;
    always #(cpu_half) cpu_clk = ~cpu_clk;
`endif

`ifdef SMP
`elsif RD68041
    logic trace;
    initial trace = $test$plusargs("trace");
    always @(posedge cpu_clk) if (trace) begin
        if (!dut.bench040_bus_ts_n)
            $display("TS  a=%08x siz=%0d rw=%0d tt=%0d tm=%0d", dut.bench040_bus_a,
                     dut.bench040_bus_siz, dut.bench040_bus_rw, dut.bench040_bus_tt, dut.bench040_bus_tm);
        if (!dut.bench040_bus_ta_n)
            $display("TA  d=%08x/%08x", dut.bench040_bus_d_r, dut.bench040_bus_d_w);
    end
`endif

`ifdef SMP
    logic [31:0] stat_contention, stat_turnaround, stat_owner, stat_atomicity, stat_intervene,
                 stat_maxwait, stat_ts0, stat_ts1, stat_handovers, stat_locked, stat_miwait;
`endif

    bench dut (
        .sys_clk(clk), .sys_rst(rst),
`ifdef FIFO_BRIDGE
        .cpu_clk(cpu_clk), .cpu_rst(rst),
`endif
        .tty_stb(tty_stb), .tty_data(tty_data),
        .exit_stb(exit_stb), .exit_code(exit_code),
        .result_stb(result_stb), .result(result)
`ifdef SMP
        , .stat_contention(stat_contention), .stat_turnaround(stat_turnaround),
        .stat_owner(stat_owner), .stat_atomicity(stat_atomicity),
        .stat_intervene(stat_intervene), .stat_maxwait(stat_maxwait), .stat_ts0(stat_ts0),
        .stat_ts1(stat_ts1), .stat_handovers(stat_handovers), .stat_locked(stat_locked),
        .stat_miwait(stat_miwait)
`endif
    );


    // RD68031 needs a 50 % duty cycle: both edges are bus states (RD68041: only
    // the rising edge).
    always #5 clk = ~clk;

    string       ram_file, vcd_file;
    logic [31:0] expected;
    logic        have_expected, seen_result;
    longint      max_cycles, cycles;

`ifdef SMP
    int          iters, nfail;
    bit          smp_done, gapless;
    initial begin
        if (!$value$plusargs("iters=%d", iters)) iters = 6;
        gapless  = $test$plusargs("gapless");
        smp_done = 0;
        nfail    = 0;
    end
    function automatic logic [31:0] peek(input logic [31:0] a);
        peek = dut.ram[a[17:2]];
    endfunction
    task automatic expect_mem(input string what, input logic [31:0] a, input logic [31:0] v);
        if (peek(a) !== v) begin
            $display("  FAIL: %s at %h is %h, expected %h", what, a, peek(a), v);
            nfail++;
        end
    endtask
    task automatic smp_verdict();
        logic [31:0] two_n;
        two_n = 32'(2 * iters);
        $display("  bus: %0d transfers by CPU 0, %0d by CPU 1; %0d grants; %0d locked sequences; longest grant wait %0d; %0d clocks waiting for MI",
                 stat_ts0, stat_ts1, stat_handovers, stat_locked, stat_maxwait, stat_miwait);
        $display("  checks: contention %0d, turnaround %0d, owner %0d, atomicity %0d, snoop interventions %0d",
                 stat_contention, stat_turnaround, stat_owner, stat_atomicity, stat_intervene);
        $display("  misaligned long word across two lines: %0d racing reads torn (allowed)", peek(32'h8074));
        if (gapless) begin
            if (stat_turnaround > 0) begin
                $display("PASS: the printed arbiter's handover with no clock between seen (%0d times)",
                         stat_turnaround);
                $finish;
                return;         // ($finish does not end the task)
            end
            $display("FAIL: +gapless: no handover with no clock between seen");
            $fatal(1);
        end
        for (int i = 0; i < 2; i++)
            if (peek(32'h8020 + 4 * i) != 0) begin
                $display("  FAIL: CPU %0d counted %0d errors, the first %h", i,
                         peek(32'h8020 + 4 * i), peek(32'h8028 + 4 * i));
                nfail++;
            end
        expect_mem("CPU 0's interrupts taken",   32'h8040, 32'(iters));
        expect_mem("CPU 1's interrupts taken",   32'h8044, 32'(iters + 1));
        expect_mem("CPU 1's shootdown",          32'h804C, 32'h1);
        expect_mem("CAS lock, cache-inhibited",  32'h8054, two_n);
        expect_mem("TAS lock, cache-inhibited",  32'h805C, two_n);
        expect_mem("lock-free, cache-inhibited", 32'h8060, two_n);
        expect_mem("CAS lock, write-through",    32'h7004, two_n);
        expect_mem("TAS lock, write-through",    32'h7014, two_n);
        expect_mem("CAS lock, page 6",           32'h6004, two_n);
        expect_mem("TAS lock, page 6",           32'h6014, two_n);
        expect_mem("lock-free, page 6",          32'h6020, two_n);
        expect_mem("CPU 0's false-sharing count", 32'h6100, 32'(iters));
        expect_mem("CPU 1's false-sharing count", 32'h6104, 32'(iters));
        expect_mem("the producer's last SEQ",    32'h7100, 32'(iters));
        expect_mem("CAS word beside counters",   32'h6300, two_n);
        expect_mem("CPU 0's counter beside it",  32'h6304, 32'(iters));
        expect_mem("CPU 1's counter beside it",  32'h6308, 32'(iters));
        for (int k = 0; k < 4; k++)
            expect_mem("MOVE16's copy", 32'h6280 + 4 * k, 32'h1616_0000 + k);
        expect_mem("the rewritten routine",      32'h4000, 32'h7002_4E75);
        nfail += stat_contention + stat_turnaround + stat_owner + stat_atomicity + stat_intervene;
        if (nfail == 0) begin
            $display("PASS: two processors, %0d iterations: interrupts, locks, coherence, the instruction cache, a TLB shootdown",
                     iters);
            $finish;
            return;
        end
        $display("FAIL: %0d checks failed", nfail);
        $fatal(1);
    endtask
    always @(posedge clk) if (!rst && !smp_done &&
        ((peek(32'h8030) == 32'hC0DE_0000 && peek(32'h8034) == 32'hC0DE_0000) ||
         (gapless && stat_turnaround > 0))) begin
        smp_done = 1;
        repeat (64) @(posedge clk);
        $display("DONE after %0d clocks", cycles);
        smp_verdict();
    end
`endif

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
