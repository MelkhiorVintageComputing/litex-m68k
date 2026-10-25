// SPDX-License-Identifier: BSD-2-Clause
// Copyright 2026 Romain Dolbeau

// litex_sim only, for debugging (RD68041_TRACE=1 when the SoC is built,
// litex_68k/cpu/rd68041/core.py): every instruction boundary in user mode
// ("U <next pc>"), every store the data unit takes in user mode ("S <addr>
// <data> <size>"), the first 30 stores after entering supervisor mode, the
// exception's frame ("F <addr> <data> <size>"), and every change of mode ("K <next pc> <sr>" on entering
// supervisor mode, "E <next pc> <sr>" on leaving it), on the simulation's
// standard output, flushed on each entry to supervisor mode. It reads the core's signals by hierarchical reference from
// the module that instantiates it, beside rd68041_top: RD68041's own lockstep
// reads the same ones (Inputs/RD68041/sim/virt/public.vlt).
module rd68041_trace (input logic clk);
    logic sup_q, sup_c;
    int   frame_n;
    logic [31:0] mmusr_q;
    initial sup_q = 1'b1;
    initial sup_c = 1'b1;
    initial frame_n = 0;
    always @(posedge clk) begin
        if (rd68041_top.u_core.retire) begin
            if (!rd68041_top.u_core.sr[13])
                $display("U %08x", rd68041_top.u_core.retire_npc);
            if (rd68041_top.u_core.sr[13] != sup_q) begin
                $display("%s %08x %04x", rd68041_top.u_core.sr[13] ? "K" : "E",
                         rd68041_top.u_core.retire_npc, rd68041_top.u_core.sr);
                $fflush();      // a fault ends in supervisor mode: keep what led to it
            end
            sup_q <= rd68041_top.u_core.sr[13];
        end
        // The first stores after entering supervisor mode (on the clock SR
        // changes, before any boundary): the exception's frame.
        sup_c <= rd68041_top.u_core.sr[13];
        if (rd68041_top.u_core.sr[13] && !sup_c)
            frame_n <= 30;
        else if (rd68041_top.u_core.u_dmu.st_ready && rd68041_top.u_core.sr[13] && frame_n != 0) begin
            $display("F %08x %08x %0d", rd68041_top.u_core.u_dmu.st_addr,
                     rd68041_top.u_core.u_dmu.st_data, rd68041_top.u_core.u_dmu.st_sz);
            if (frame_n == 1)
                $fflush();
            frame_n <= frame_n - 1;
        end
        if (rd68041_top.u_core.u_dmu.st_ready && !rd68041_top.u_core.sr[13])
            $display("S %08x %08x %0d", rd68041_top.u_core.u_dmu.st_addr,
                     rd68041_top.u_core.u_dmu.st_data, rd68041_top.u_core.u_dmu.st_sz);
        // The instruction ATC: its fills ("I <la> s=<s> r=<resident>") and
        // flushes ("J <la or all>"), for the user half of the address space,
        // and MMUSR as PTEST leaves it ("M <mmusr>").
        if (rd68041_top.u_core.u_ifu.u_iatc.fill_we && rd68041_top.u_core.u_ifu.u_iatc.fill_la[31])
            $display("I %05x000 s=%0d r=%0d", rd68041_top.u_core.u_ifu.u_iatc.fill_la,
                     rd68041_top.u_core.u_ifu.u_iatc.fill_s, rd68041_top.u_core.u_ifu.u_iatc.fill_r);
        if (rd68041_top.u_core.u_ifu.u_iatc.fl_all)
            $display("J all");
        if (rd68041_top.u_core.u_ifu.u_iatc.fl_page && rd68041_top.u_core.u_ifu.u_iatc.lk_la[31])
            $display("J %05x000", rd68041_top.u_core.u_ifu.u_iatc.lk_la);
        mmusr_q <= rd68041_top.u_core.mmusr;
        if (rd68041_top.u_core.mmusr != mmusr_q)
            $display("M %08x", rd68041_top.u_core.mmusr);
    end
endmodule
