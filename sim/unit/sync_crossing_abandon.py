#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright 2026 Romain Dolbeau

# WishboneSyncCrossing, with abandoned accesses: the slave ignores address 15;
# the master gives up after 6 clocks. Every other access: exactly once.
import random
from migen import *
from litex.soc.interconnect import wishbone
from litex_68k.cpu.rd68031.core import WishboneSyncCrossing

m = wishbone.Interface(data_width=32, address_width=32, addressing="word")
s = wishbone.Interface(data_width=32, address_width=32, addressing="word")
dut = WishboneSyncCrossing(m, s, "cpu")
random.seed(2)
mem, slave_log, master_log = {}, [], []
@passive
def slave():
    while True:
        if (yield s.cyc) and (yield s.stb):
            if (yield s.adr) == 15:
                while (yield s.cyc):
                    yield
                continue
            for _ in range(random.choice([0, 0, 1, 2, 5])):
                yield
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
def master():
    ref, abandoned = {}, 0
    for i in range(400):
        adr = random.randrange(16)
        we = random.random() < 0.5
        dat = random.getrandbits(32)
        yield m.adr.eq(adr); yield m.we.eq(we); yield m.dat_w.eq(dat)
        yield m.cyc.eq(1); yield m.stb.eq(1)
        n, acked = 0, False
        while n < 6:
            yield
            n += 1
            if (yield m.ack):
                acked = True
                break
        if acked:
            got = (yield m.dat_r)
            if we:
                ref[adr] = dat
            else:
                assert got == ref.get(adr, 0xdead0000 | adr), (i, hex(got))
            master_log.append((adr, int(we)))
        else:
            assert adr == 15, ("no answer", i, adr)
            abandoned += 1
            yield m.cyc.eq(0); yield m.stb.eq(0)
            yield                       # abandoning is dropping cyc, for a clock at least
        yield m.cyc.eq(0); yield m.stb.eq(0)
        for _ in range(random.choice([0, 1, 3])):
            yield
    print("abandoned %d" % abandoned)
dut.clock_domains.cd_sys = ClockDomain()
dut.clock_domains.cd_cpu = ClockDomain()
run_simulation(dut, {"sys": [slave()], "cpu": [master()]}, clocks={"sys": 10, "cpu": 40})
assert [(a, int(w)) for a, w in slave_log] == master_log, "an access was not made exactly once"
print("%d answered accesses, each once at the slave" % len(master_log))
