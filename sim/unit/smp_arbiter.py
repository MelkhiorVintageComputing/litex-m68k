#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright 2026 Romain Dolbeau

# M68040FairArbiter (litex_68k/m68040_smp.py) against the table of RD68041's
# doc/um-7-8-2-1-fairness-arbitration.md section 6.1, written out here as a
# Python model: random BR1, BR2, BB, LOCK and LOCKE every clock, with LOCKE
# used or not and the printed (gapless) grants or the corrected ones; then
# directed runs: requests granted in turn, and a clock with neither grant at
# every handover unless gapless.
import random

from migen import *
from litex_68k.m68040_smp import M68040FairArbiter

A, B, C, D = range(4)

def model_next(state, br1, br2, bb, lock, locke, use_locke):
    """The table, inputs true when asserted."""
    locke = locke and use_locke
    if state == A:
        return B if br2 and (not lock or locke) else A
    if state == B:
        return C if not bb else A if lock and not locke else B
    if state == C:
        return D if br1 and (not lock or locke) else C
    return A if not bb else C if lock and not locke else D

def model_grants(state, gapless):
    return (state == A or (gapless and state == D), state == C or (gapless and state == B))

def lockstep_run(use_locke, gapless, clocks=20000, seed=2):
    """One clock per step: inputs set, the edge, outputs compared."""
    rng = random.Random(seed)
    dut = M68040FairArbiter(use_locke=use_locke, gapless=gapless)
    bad = []
    arcs = set()
    def gen():
        state = A
        for n in range(clocks):
            br1, br2, bb, lock, locke = [rng.random() < 0.5 for _ in range(5)]
            yield dut.br_n[0].eq(not br1)
            yield dut.br_n[1].eq(not br2)
            yield dut.bb_n.eq(not bb)
            yield dut.lock_n.eq(not lock)
            yield dut.locke_n.eq(not locke)
            yield
            # Migen's simulator: what the edge after these inputs registered
            # is read one step later, so this step reads the previous step's.
            got = ((yield dut.state), not (yield dut.bg_n[0]), not (yield dut.bg_n[1]))
            want = (state, *model_grants(state, gapless))
            if got != want and len(bad) < 4:
                bad.append((n, got, want))
            nxt = model_next(state, br1, br2, bb, lock, locke, use_locke)
            arcs.add((state, nxt))
            state = nxt
    run_simulation(dut, gen())
    return bad, arcs

# Every arc of the table, with and without LOCKE, both grant styles.
ALL_ARCS = {(A, A), (A, B), (B, B), (B, C), (B, A), (C, C), (C, D), (D, D), (D, A), (D, C)}
for use_locke in (True, False):
    for gapless in (False, True):
        bad, arcs = lockstep_run(use_locke, gapless)
        assert not bad, ("mismatch", use_locke, gapless, bad)
        assert arcs == ALL_ARCS, ("arcs not taken", ALL_ARCS - arcs)
        print("random, use_locke={} gapless={}: 20000 clocks as the table, every arc taken".format(
            use_locke, gapless))

# Directed: both processors keep requesting, with BB asserted while one owns
# the bus and negated a clock after its grant goes: ownership alternates, and
# (corrected) a clock with neither grant between tenures.
def alternate(gapless):
    dut = M68040FairArbiter(gapless=gapless)
    grants = []
    def gen():
        yield dut.br_n[0].eq(0)
        yield dut.br_n[1].eq(0)
        owner = 0
        for n in range(60):
            g0, g1 = not (yield dut.bg_n[0]), not (yield dut.bg_n[1])
            grants.append((g0, g1))
            # The owner holds BB while it is granted, and lets go a clock later.
            mine = g0 if owner == 0 else g1
            yield dut.bb_n.eq(0 if mine else 1)
            if g0: owner = 0
            if g1: owner = 1
            yield
    run_simulation(dut, gen())
    return grants

g = alternate(gapless=False)
tenures = [k for k in range(1, len(g)) if g[k] != g[k - 1] and (g[k][0] or g[k][1])]
assert len(tenures) >= 10, ("ownership does not alternate", g)
for k in tenures:
    assert g[k - 1] == (False, False), ("a grant with no clock between", k, g[k - 1], g[k])
owners = [0 if g[k][0] else 1 for k in tenures]
assert all(owners[i] != owners[i + 1] for i in range(len(owners) - 1)), owners
print("alternating requests: {} tenures in turn, each after a clock with neither grant".format(len(tenures)))

g = alternate(gapless=True)
direct = [k for k in range(1, len(g)) if g[k - 1] != (False, False) and g[k] != g[k - 1]
          and (g[k][0] or g[k][1]) and (g[k - 1][0] or g[k - 1][1])]
assert direct, "gapless: no handover with no clock between"
print("gapless: {} handovers with no clock between (the printed figure's F3)".format(len(direct)))
print("smp_arbiter: ok")
