#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright 2026 Romain Dolbeau

# WishboneErrorTimeout: an answered access passes, an unanswered one ends with
# err and cyc dropped to the slave, and the next one works.
from migen import *
from litex.soc.interconnect import wishbone
from litex_68k.cpu.rd68031.core import WishboneErrorTimeout

m = wishbone.Interface(data_width=32, address_width=32, addressing="word")
s = wishbone.Interface(data_width=32, address_width=32, addressing="word")
dut = WishboneErrorTimeout(m, s, 8)
answer = Signal()          # does the slave answer?
dut.comb += [s.ack.eq(answer & s.cyc & s.stb), s.dat_r.eq(0x12345678)]
log = []
def gen():
    for want_answer in (1, 0, 1):
        yield answer.eq(want_answer)
        yield m.cyc.eq(1); yield m.stb.eq(1); yield m.adr.eq(0x100)
        n = 0
        while True:
            yield
            n += 1
            ack, err, scyc = (yield m.ack), (yield m.err), (yield s.cyc)
            if ack or err:
                log.append((want_answer, n, ack, err, scyc, hex((yield m.dat_r))))
                break
            assert n < 50
        yield m.cyc.eq(0); yield m.stb.eq(0)
        yield; yield
run_simulation(dut, gen())
for l in log: print("answer=%d after %d clocks: ack=%d err=%d slave.cyc=%d dat_r=%s" % l)
assert [(a, ack, err, cyc) for a, n, ack, err, cyc, _ in log] == [(1, 1, 0, 1), (0, 0, 1, 0), (1, 1, 0, 1)], log
assert log[1][1] == 8, "the time-out is not 8 clocks"
print("error_timeout: ok")
