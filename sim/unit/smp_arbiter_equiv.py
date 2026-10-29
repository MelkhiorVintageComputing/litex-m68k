#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright 2026 Romain Dolbeau

# M68040FairArbiter (litex_68k/m68040_smp.py) and RD68041's own
# sim/models/rd68041_fairarb.sv, side by side under iverilog: the same random
# BR1, BR2, BB, LOCK and LOCKE every clock, and the grants, the state and
# `who` compared on every clock, for each use_locke and gapless. The SV model
# is read from Inputs/RD68041 as it is; nothing is copied from it.
import os
import subprocess
import sys

from migen import *
from migen.fhdl.verilog import convert

from litex_68k.m68040_smp import M68040FairArbiter

TOP   = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MODEL = os.path.join(TOP, "Inputs", "RD68041", "sim", "models", "rd68041_fairarb.sv")
BUILD = os.path.join(TOP, "build", "unit", "smp_arbiter_equiv")
CLOCKS = 200000

TB = """
`timescale 1ns/1ps
module tb;
  logic clk = 0, rst = 1;
  always #5 clk = ~clk;
  logic br1_n = 1, br2_n = 1, bb_n = 1, lock_n = 1, locke_n = 1;
  logic m_bg1_n, m_bg2_n, m_who; logic [1:0] m_state;
  logic r_bg1_n, r_bg2_n, r_who; logic [1:0] r_state;
  migen_fairarb u_m (.sys_clk(clk), .sys_rst(rst), .br0_n(br1_n), .br1_n(br2_n),
      .bb_n(bb_n), .lock_n(lock_n), .locke_n(locke_n), .bg0_n(m_bg1_n), .bg1_n(m_bg2_n),
      .state(m_state), .who(m_who));
  rd68041_fairarb u_r (.clk(clk), .rst_n(!rst), .use_locke(1'b%(use_locke)d),
      .gapless(1'b%(sv_gapless)d), .br1_n(br1_n), .br2_n(br2_n), .bb_n(bb_n), .lock_n(lock_n),
      .locke_n(locke_n), .bg1_n(r_bg1_n), .bg2_n(r_bg2_n), .state(r_state), .who(r_who));
  int n = 0, bad = 0, seed = %(seed)d;
  int seen [0:3];
  initial begin
    for (int i = 0; i < 4; i++) seen[i] = 0;
    void'($urandom(seed));
    repeat (3) @(negedge clk);
    rst = 0;
    while (n < %(clocks)d) begin
      @(negedge clk);
      if ({m_bg1_n, m_bg2_n, m_state, m_who} !== {r_bg1_n, r_bg2_n, r_state, r_who}) begin
        bad++;
        if (bad <= 4)
          $display("MISMATCH at %%0d: migen bg %%b%%b state %%0d who %%b, rd68041 bg %%b%%b state %%0d who %%b",
                   n, m_bg1_n, m_bg2_n, m_state, m_who, r_bg1_n, r_bg2_n, r_state, r_who);
      end
      seen[r_state]++;
      {br1_n, br2_n, bb_n, lock_n, locke_n} = $urandom;
      n++;
    end
    $display("RESULT clocks %%0d mismatches %%0d states %%0d %%0d %%0d %%0d",
             n, bad, seen[0], seen[1], seen[2], seen[3]);
    $finish;
  end
endmodule
"""

def run(use_locke, gapless, seed, sv_gapless=None):
    """True if the two agree on every clock. `sv_gapless`, given, runs the SV
    model with another `gapless` than Migen's: a negative run."""
    sv_gapless = gapless if sv_gapless is None else sv_gapless
    out = os.path.join(BUILD, "locke{}_gapless{}{}".format(int(use_locke), int(gapless), int(sv_gapless)))
    os.makedirs(out, exist_ok=True)
    arb = M68040FairArbiter(use_locke=use_locke, gapless=gapless)
    ios = {*arb.br_n, arb.bb_n, arb.lock_n, arb.locke_n, *arb.bg_n, arb.state, arb.who}
    with open(os.path.join(out, "migen_fairarb.v"), "w") as f:
        f.write(str(convert(arb, ios=ios, name="migen_fairarb")))
    with open(os.path.join(out, "tb.sv"), "w") as f:
        f.write(TB % dict(use_locke=use_locke, sv_gapless=sv_gapless, seed=seed, clocks=CLOCKS))
    subprocess.check_call(["iverilog", "-g2012", "-o", os.path.join(out, "tb.vvp"),
                           os.path.join(out, "tb.sv"), os.path.join(out, "migen_fairarb.v"), MODEL])
    res = subprocess.run(["vvp", "-n", os.path.join(out, "tb.vvp")], capture_output=True, text=True)
    lines = [l for l in res.stdout.splitlines() if l.startswith(("RESULT", "MISMATCH"))]
    print("use_locke={} gapless={}: {}".format(use_locke, gapless, "; ".join(lines)))
    result = [l for l in lines if l.startswith("RESULT")]
    if not result:
        return False
    fields = result[0].split()
    return fields[4] == "0" and all(int(x) > 0 for x in fields[6:10])

ok = all([run(l, g, seed=11 + 2 * l + g) for l in (True, False) for g in (False, True)])
# The comparison can fail: the printed grants against the corrected ones.
print("negative run, the SV model with the corrected grants:")
ok &= not run(True, True, seed=17, sv_gapless=False)
print("smp_arbiter_equiv:", "ok" if ok else "FAIL")
sys.exit(0 if ok else 1)
