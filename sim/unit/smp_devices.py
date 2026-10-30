#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright 2026 Romain Dolbeau

# M68040SMPBus's devices and MI gate (litex_68k/m68040_smp.py), with two fake
# processors driving their pins as an MC68040 does for a long-word transfer:
# WHOAMI for each processor, BOOT_PC, the interprocessor interrupts, RELEASE,
# TCI on reads; no TA while the other processor's MI is asserted, unless it
# has been in reset since its last TS (the gate); the counters, and their
# clearing; the SoC's reset request clearing RELEASE, BOOT_PC and the
# interprocessor interrupts.
from migen import *

from litex_68k.m68040_bus import m68040_bus_layout
from litex_68k.m68040_smp import (M68040SMPBus, m68040_pins_layout, DEVICES_BASE, REG_WHOAMI,
    REG_IPI_SET, REG_IPI_ACK, REG_RELEASE, REG_BOOT_PC, REG_STATS, STATS)

cpus = [Record(m68040_pins_layout, name="cpu{}".format(i)) for i in range(2)]
bus  = Record(m68040_bus_layout)
dut  = M68040SMPBus(cpus, bus)
# The bridge, idle: it never answers here.
dut.comb += [bus.ta_n.eq(1), bus.tea_n.eq(1), bus.tci_n.eq(1), bus.tbi_n.eq(1), bus.avec_n.eq(1),
             bus.d_r.eq(0)]

def idle(c):
    for f, v in (("ts_n_o", 1), ("ts_oe", 0), ("a_oe", 0), ("attr_oe", 0), ("d_oe", 0),
                 ("tip_oe", 0), ("bb_oe", 0), ("ta_oe", 0), ("br_n_o", 1), ("lock_n_o", 1),
                 ("locke_n_o", 1), ("ciout_n_o", 1), ("mi_n_o", 1)):
        yield getattr(c, f).eq(v)

def transfer(i, reg, write=None, limit=40):
    """CPU i's long-word transfer to a device register: TS for a clock with
    the address and attributes, the data from C2 for a write, until TA.
    Returns (clocks to TA, the data read, TCI) or None if no TA came."""
    c = cpus[i]
    yield c.a_o.eq(DEVICES_BASE + reg); yield c.a_oe.eq(1)
    yield c.attr_oe.eq(1); yield c.tt_o.eq(0); yield c.siz_o.eq(0)
    yield c.rw_o.eq(write is None)
    yield c.ts_oe.eq(1); yield c.ts_n_o.eq(0)
    yield
    yield c.ts_n_o.eq(1)
    if write is not None:
        yield c.d_o.eq(write); yield c.d_oe.eq(1)
    result = None
    for n in range(limit):
        yield
        if not (yield dut.ta_n):
            result = (n, (yield dut.d), (yield dut.tci_n))
            break
    yield
    yield from idle(c)
    yield
    return result

def grant(i):
    """Make the arbiter give CPU i the bus: it asks, the other does not."""
    yield cpus[i].br_n_o.eq(0); yield cpus[1 - i].br_n_o.eq(1)
    for _ in range(8):
        yield
    yield cpus[i].br_n_o.eq(1)
    yield

log = {}
def gen():
    for c in cpus:
        yield from idle(c)
        yield c.rsti_n.eq(1)
    yield; yield
    # The records start at 0, so both "asked" at time 0: give CPU 0 the bus.
    yield from grant(0)
    yield from transfer(0, REG_STATS, write=0)      # and clear the counters
    r = yield from transfer(0, REG_WHOAMI)
    log["whoami0"] = r
    yield from transfer(0, REG_BOOT_PC, write=0x40001234)
    log["boot_pc"] = yield from transfer(0, REG_BOOT_PC)
    yield from transfer(0, REG_IPI_SET, write=1)
    log["ipi_after_set1"] = (yield dut.devices.ipi)
    yield from transfer(0, REG_RELEASE, write=1)
    log["release"] = (yield dut.devices.release)
    # CPU 1 takes the bus, asks who it is, and acknowledges its interrupt.
    yield from grant(1)
    log["whoami1"] = yield from transfer(1, REG_WHOAMI)
    yield from transfer(1, REG_IPI_ACK, write=0)
    log["ipi_after_ack1"] = (yield dut.devices.ipi)
    # MI: CPU 1 has driven TS, so its MI counts; asserted, memory waits.
    yield cpus[1].mi_n_o.eq(0)
    yield from grant(0)
    log["mi_blocked"] = yield from transfer(0, REG_WHOAMI, limit=20)
    # The gate: CPU 1 in reset, its MI ignored until it drives TS again.
    yield cpus[1].rsti_n.eq(0)
    yield
    log["mi_gated"] = yield from transfer(0, REG_WHOAMI)
    yield cpus[1].rsti_n.eq(1)
    yield
    log["mi_still_gated"] = yield from transfer(0, REG_WHOAMI)
    yield cpus[1].mi_n_o.eq(1)
    yield from transfer(0, REG_RELEASE, write=0)
    log["held"] = (yield dut.devices.release)
    # The SoC's reset request (ctrl's cpu_rst): CPU 1 back in reset, and no
    # BOOT_PC to send it to.
    yield from transfer(0, REG_BOOT_PC, write=0x40005678)
    yield from transfer(0, REG_IPI_SET, write=1)
    yield from transfer(0, REG_RELEASE, write=1)
    log["before_reset"] = ((yield dut.devices.release), (yield dut.devices.boot_pc), (yield dut.devices.ipi))
    yield dut.reset.eq(1)
    yield
    yield dut.reset.eq(0)
    yield; yield
    log["after_reset"] = ((yield dut.devices.release), (yield dut.devices.boot_pc), (yield dut.devices.ipi))
    # The counters.
    log["stats"] = {}
    for name in STATS:
        log["stats"][name] = (yield dut.stats[name])
    yield cpus[0].a_oe.eq(1); yield cpus[1].a_oe.eq(1)
    yield
    yield cpus[0].a_oe.eq(0); yield cpus[1].a_oe.eq(0)
    yield; yield
    log["contention"] = (yield dut.stats["contention"])
    yield from transfer(0, REG_STATS, write=0)
    yield
    log["cleared"] = {}
    for name in STATS:
        log["cleared"][name] = (yield dut.stats[name])

run_simulation(dut, gen())
for k, v in log.items():
    print("{:15s} {}".format(k, v))

assert log["whoami0"] is not None and log["whoami0"][1] == 0 and log["whoami0"][2] == 0, log["whoami0"]
assert log["boot_pc"][1] == 0x40001234
assert log["ipi_after_set1"] == 0b10
assert log["release"] == 1
assert log["whoami1"][1] == 1, "CPU 1's WHOAMI"
assert log["ipi_after_ack1"] == 0b00
assert log["mi_blocked"] is None, "TA while the other processor's MI was asserted"
assert log["mi_gated"] is not None and log["mi_still_gated"] is not None, "the MI gate"
assert log["held"] == 0
assert log["before_reset"] == (1, 0x40005678, 0b10), log["before_reset"]
assert log["after_reset"] == (0, 0, 0), "the reset request: " + str(log["after_reset"])
s = log["stats"]
assert s["contention"] == 0 and s["owner"] == 0 and s["atomicity"] == 0 and s["intervene"] == 0, s
assert s["ts0"] >= 10 and s["ts1"] == 2 and s["miwait"] == 0, s
assert log["contention"] == 1
assert all(v == 0 for k, v in log["cleared"].items() if k not in ("ts0", "miwait")), log["cleared"]
print("smp_devices: ok")
