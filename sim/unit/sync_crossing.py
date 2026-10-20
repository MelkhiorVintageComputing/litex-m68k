#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright 2026 Romain Dolbeau

# WishboneSyncCrossing: master at sys/4 ("cpu"), slave in sys with random wait
# states; every access must reach the slave exactly once, with the right data.
import random
from migen import *
from litex.soc.interconnect import wishbone
from litex_68k.cpu.rd68031.core import WishboneSyncCrossing

m = wishbone.Interface(data_width=32, address_width=32, addressing="word")
s = wishbone.Interface(data_width=32, address_width=32, addressing="word")
dut = WishboneSyncCrossing(m, s, "cpu")
random.seed(1)
mem = {}
slave_log = []
@passive
def slave():
    while True:
        if (yield s.cyc) and (yield s.stb):
            for _ in range(random.choice([0, 0, 1, 2, 5])):
                yield
                assert (yield s.cyc)
            adr, we = (yield s.adr), (yield s.we)
            if we:
                mem[adr] = (yield s.dat_w)
            else:
                yield s.dat_r.eq(mem.get(adr, 0xdead0000 | adr))
            slave_log.append((adr, we))
            yield s.ack.eq(1)
            yield
            yield s.ack.eq(0)
        yield
master_log = []
def master():
    ref = {}
    for i in range(300):
        adr = random.randrange(16)
        we = random.random() < 0.5
        dat = random.getrandbits(32)
        yield m.adr.eq(adr); yield m.we.eq(we); yield m.dat_w.eq(dat)
        yield m.cyc.eq(1); yield m.stb.eq(1)
        n = 0
        while True:
            yield
            n += 1
            if (yield m.ack):
                break
            assert n < 20, "no ack"
        got = (yield m.dat_r)
        if we:
            ref[adr] = dat
        else:
            assert got == ref.get(adr, 0xdead0000 | adr), (i, hex(got))
        master_log.append((adr, int(we), n))
        yield m.cyc.eq(0); yield m.stb.eq(0)
        for _ in range(random.choice([0, 1, 3])):
            yield
dut.clock_domains.cd_sys = ClockDomain()
dut.clock_domains.cd_cpu = ClockDomain()
run_simulation(dut, {"sys": [slave()], "cpu": [master()]}, clocks={"sys": 10, "cpu": 40})
assert [(a, int(w)) for a, w in slave_log] == [(a, w) for a, w, _ in master_log], "an access was not made exactly once"
lat = [n for _, _, n in master_log]
print("%d accesses, each once at the slave; master clocks to ack: min %d, max %d, mean %.2f" % (len(lat), min(lat), max(lat), sum(lat) / len(lat)))
