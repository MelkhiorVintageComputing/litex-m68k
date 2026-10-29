# SPDX-License-Identifier: BSD-2-Clause
# Copyright 2026 Romain Dolbeau

"""Two MC68040s on one bus: the board's logic (L18, doc/l18-smp.md).

What a board with two MC68040s needs beyond the processors' own pins, after
RD68041's doc/smp.md (its "setup A"), and the MC68040 User's Manual (UM),
sections 4.4-4.5 (snooping), 5 (signals) and 7.8 (arbitration):

- The bus, resolved (`M68040SMPBus`). Inside an FPGA nothing is three-state:
  a bussed active-low line is the AND of what each driver drives, a driver
  that does not drive it counting as the board's pull-up; a bussed group (A,
  the attributes, D) is whichever driver enables it. SC1-SC0, the snoop
  operation each processor performs on the other's transfers, is the master's
  UPA1-UPA0 straight (setup A: every shared page written through with U = 01,
  so no snooped line is ever dirty and nothing is ever sourced).
- The MI gate. Memory waits for both processors' MI negated (UM 5.5.2,
  7.9.1). A processor asserts MI from its reset until its first bus cycle, so
  each processor's MI is ignored from its reset until it first drives TS
  (doc/smp.md item 1: without it, a processor held in reset, or let out of it
  in the middle of the other's transfer, stops memory for ever).
- `M68040FairArbiter`, UM 7.8.2.1's dual-processor fairness arbiter, as
  RD68041's doc/um-7-8-2-1-fairness-arbitration.md corrects it (its section
  6.1): a clock with neither grant at every change of ownership, and a locked
  sequence kept whole.
- `M68040SMPDevices`, the board's registers, at DEVICES_BASE: which
  processor is asking, the interprocessor interrupts, CPU 1's release from
  reset and where it starts, and counters of what the bus did. Its first four
  registers are those of RD68041's two-processor bench, so that RD68041's own
  test program runs on this board unchanged (sim/bench, SMP=1).

Everything is in the processors' clock domain.
"""

from functools import reduce
from operator import and_, or_

from migen import *
from migen.genlib.record import DIR_M_TO_S, DIR_S_TO_M

from litex.gen import *

# A processor's pins, as the board sees them ---------------------------------------------------

# rd68041_top's outputs (DIR_M_TO_S) and the inputs the board gives each
# processor on its own (DIR_S_TO_M); the bussed inputs are the resolved bus.
m68040_pins_layout = [
    ("a_o",       32, DIR_M_TO_S), ("a_oe",      1, DIR_M_TO_S),
    ("d_o",       32, DIR_M_TO_S), ("d_oe",      1, DIR_M_TO_S),
    ("tt_o",       2, DIR_M_TO_S), ("tm_o",      3, DIR_M_TO_S),
    ("upa_o",      2, DIR_M_TO_S), ("rw_o",      1, DIR_M_TO_S),
    ("siz_o",      2, DIR_M_TO_S), ("lock_n_o",  1, DIR_M_TO_S),
    ("locke_n_o",  1, DIR_M_TO_S), ("ciout_n_o", 1, DIR_M_TO_S),
    ("attr_oe",    1, DIR_M_TO_S),
    ("ts_n_o",     1, DIR_M_TO_S), ("ts_oe",     1, DIR_M_TO_S),
    ("tip_n_o",    1, DIR_M_TO_S), ("tip_oe",    1, DIR_M_TO_S),
    ("ta_n_o",     1, DIR_M_TO_S), ("ta_oe",     1, DIR_M_TO_S),
    ("mi_n_o",     1, DIR_M_TO_S), ("br_n_o",    1, DIR_M_TO_S),
    ("bb_n_o",     1, DIR_M_TO_S), ("bb_oe",     1, DIR_M_TO_S),
    ("bg_n_i",     1, DIR_S_TO_M), ("rsti_n",    1, DIR_S_TO_M),
]

DEVICES_BASE = 0xFFFF_0000
DEVICES_SIZE = 0x100

# The registers, as offsets from DEVICES_BASE (doc/l18-smp.md).
REG_WHOAMI     = 0x00  # R: the processor making the access, 0 or 1
REG_IPI_SET    = 0x04  # W: raise CPU n's interprocessor interrupt (n = bit 0)
REG_IPI_ACK    = 0x08  # W: lower the writer's own
REG_RELEASE    = 0x0C  # W: non-zero lets CPU 1 out of reset, zero holds it there; R: which
REG_BOOT_PC    = 0x10  # RW: where crt0 sends CPU 1
REG_BOOT_SP    = 0x14  # RW: its stack
REG_NCPUS      = 0x18  # R: 2
REG_STATS      = 0x20  # R: the counters, in STATS order; a write to the first clears them all
STATS = [
    "contention",  # clocks with two processors driving one bussed line or group
    "turnaround",  # the address group handed over with no clock between
    "owner",       # transfers started by a processor the arbiter had not granted
    "atomicity",   # the other processor's TS inside a locked sequence
    "intervene",   # long words a snooping processor terminated (setup A: never)
    "maxwait",     # the longest a bus request waited for its grant, in clocks
    "ts0",         # transfers started by CPU 0
    "ts1",         # ... by CPU 1
    "handovers",   # grants given
    "locked",      # locked sequences
    "miwait",      # clocks a transfer waited for MI
]

IPI_LEVEL = 5  # vector 29, autovectored

# The arbiter --------------------------------------------------------------------------------------

class M68040FairArbiter(LiteXModule):
    """UM 7.8.2.1's fairness arbiter for two MC68040s, as RD68041's
    doc/um-7-8-2-1-fairness-arbitration.md section 6.1 corrects it. Processor
    1 of the manual is CPU 0 here. Inputs are the resolved bus lines (active
    low, high when nobody drives them); the grants are registered.

    | State | BG1 | BG2 | Next |
    | A | asserted | negated | B if BR2 and (not LOCK, or LOCK and LOCKE) |
    | B | negated | negated | C if not BB; A if BB and LOCK and not LOCKE |
    | C | negated | asserted | D if BR1 and (not LOCK, or LOCK and LOCKE) |
    | D | negated | negated | A if not BB; C if BB and LOCK and not LOCKE |

    `use_locke` False takes LOCKE as always negated: the arbiter for a system
    that retries the last write of a locked sequence (the corrected text's last
    paragraph). `gapless` True grants in B and D as the printed figure does, the
    handover with no clock between (its F3): for the negative test only.

    `who` is the processor that may start a transfer: the one granted, or the
    one granted last.
    """
    A, B, C, D = range(4)

    def __init__(self, use_locke=True, gapless=False):
        self.br_n    = [Signal(reset=1, name="br{}_n".format(i)) for i in range(2)]
        self.bb_n    = Signal(reset=1)
        self.lock_n  = Signal(reset=1)
        self.locke_n = Signal(reset=1)
        # A: CPU 0 granted out of reset.
        self.bg_n    = [Signal(reset=0, name="bg0_n"), Signal(reset=1, name="bg1_n")]
        self.state   = Signal(2)
        self.who     = Signal()

        # # #

        A, B, C, D = self.A, self.B, self.C, self.D
        br1, br2 = ~self.br_n[0], ~self.br_n[1]
        bb, lock = ~self.bb_n, ~self.lock_n
        locke    = Signal()
        self.comb += locke.eq(~self.locke_n if use_locke else 0)

        nx = Signal(2)
        self.comb += [
            nx.eq(self.state),
            Case(self.state, {
                A: If(br2 & (~lock | locke), nx.eq(B)),
                B: If(~bb, nx.eq(C)).Elif(lock & ~locke, nx.eq(A)),
                C: If(br1 & (~lock | locke), nx.eq(D)),
                D: If(~bb, nx.eq(A)).Elif(lock & ~locke, nx.eq(C)),
            }),
        ]
        self.sync += [
            self.state.eq(nx),
            self.bg_n[0].eq(~((nx == A) | ((nx == D) if gapless else 0))),
            self.bg_n[1].eq(~((nx == C) | ((nx == B) if gapless else 0))),
        ]
        # (== 0, not ~: migen's simulator takes ~1 for true in a Mux.)
        self.comb += self.who.eq(Mux(self.bg_n[1] == 0, 1, Mux(self.bg_n[0] == 0, 0, self.state[1])))

# The devices --------------------------------------------------------------------------------------

class M68040SMPDevices(LiteXModule):
    """The board's registers (the module's docstring, REG_*), a slave on the
    resolved bus: a long word at a time, normal transfers (TT = 0) only. It
    answers on the clock after TS once MI lets memory answer (UM 7.9), with TA,
    and TCI on a read, so that no cache keeps a register. A write's data is
    taken on the edge the processor samples TA (UM 7.4.3: on the bus until
    then).

    `ipi[i]` raises CPU i's interrupt request; `release` lets CPU 1 out of
    reset."""
    def __init__(self, bus, who, mi_n, stats):
        self.d_o     = Signal(32)
        self.d_oe    = Signal()
        self.ta_n    = Signal(reset=1)
        self.tci_n   = Signal(reset=1)
        self.ipi     = Signal(2)
        self.release = Signal()
        self.boot_pc = Signal(32)
        self.boot_sp = Signal(32)
        self.clear   = Signal()        # a write to the first counter: clear them all

        # # #

        hit  = Signal()
        self.comb += hit.eq((bus.tt == 0) & (bus.a[8:] == (DEVICES_BASE >> 8)))
        busy = Signal()
        reg  = Signal(6)               # the long word, A7-A2
        rw   = Signal()
        xwho = Signal()

        reads = {REG_WHOAMI >> 2: xwho, REG_RELEASE >> 2: self.release,
                 REG_BOOT_PC >> 2: self.boot_pc, REG_BOOT_SP >> 2: self.boot_sp,
                 REG_NCPUS >> 2: 2}
        for k, name in enumerate(STATS):
            reads[(REG_STATS >> 2) + k] = stats[name]
        rdata = Signal(32)
        self.comb += Case(reg, dict({k: rdata.eq(v) for k, v in reads.items()},
                                    default=rdata.eq(0)))

        self.sync += [
            self.ta_n.eq(1), self.tci_n.eq(1), self.d_oe.eq(0), self.clear.eq(0),
            If(busy & ~self.ta_n,
                # The edge the processor samples TA: a write's data is on D.
                busy.eq(0),
                If(~rw,
                    Case(reg, {
                        REG_IPI_SET >> 2: If(bus.d[0], self.ipi[1].eq(1)).Else(self.ipi[0].eq(1)),
                        REG_IPI_ACK >> 2: If(xwho, self.ipi[1].eq(0)).Else(self.ipi[0].eq(0)),
                        REG_RELEASE >> 2: self.release.eq(bus.d != 0),
                        REG_BOOT_PC >> 2: self.boot_pc.eq(bus.d),
                        REG_BOOT_SP >> 2: self.boot_sp.eq(bus.d),
                        REG_STATS   >> 2: self.clear.eq(1),
                    }),
                ),
            ),
            If(~bus.ts_n & hit,
                busy.eq(1), reg.eq(bus.a[2:8]), rw.eq(bus.rw), xwho.eq(who),
            ).Elif(busy & self.ta_n & mi_n,
                self.ta_n.eq(0),
                self.tci_n.eq(~rw),
                self.d_o.eq(rdata),
                self.d_oe.eq(rw),
            ),
        ]

# The board ----------------------------------------------------------------------------------------

class M68040SMPBus(LiteXModule):
    """Two processors' pins (`cpus`, m68040_pins_layout records) on one bus,
    the arbiter, the MI gate, the devices and the counters.

    `bus` is the bridge's side (m68040_bus_layout, litex_68k/m68040_bus.py):
    this drives its processor-side fields from the resolved bus and takes what
    the bridge drives. `cpu_inputs()` gives the resolved bus as the
    processors' bussed inputs, for rd68041_top's parameters. `mi_n` is memory's MI (both
    processors', gated) for the bridge; `ignore` is the devices' window, which
    the bridge must leave to them."""
    def __init__(self, cpus, bus, use_locke=True, gapless=False, mi_gate=True):
        assert len(cpus) == 2
        self.cpus = cpus
        self.mi_n = Signal()
        self.ignore = lambda a: a[8:] == (DEVICES_BASE >> 8)

        # # #

        c0, c1 = cpus
        def mux2(field, oe, idle):
            return Mux(getattr(c0, oe), getattr(c0, field),
                       Mux(getattr(c1, oe), getattr(c1, field), idle))
        def wand(field, oe):
            return reduce(and_, [Mux(getattr(c, oe), getattr(c, field), 1) for c in cpus])

        # The resolved bus. Undriven, the active-low lines are high (pull-ups),
        # SC is 00 (pull-downs), the rest high.
        self.a      = a      = Signal(32)
        self.tt     = tt     = Signal(2)
        self.tm     = tm     = Signal(3)
        self.siz    = siz    = Signal(2)
        self.rw     = rw     = Signal()
        self.sc     = sc     = Signal(2)
        self.lock_n = lock_n = Signal()
        self.locke_n= locke_n= Signal()
        self.ciout_n= ciout_n= Signal()
        self.ts_n   = ts_n   = Signal()
        self.tip_n  = tip_n  = Signal()
        self.bb_n   = bb_n   = Signal()
        self.d      = d      = Signal(32)
        self.ta_n   = ta_n   = Signal()
        self.comb += [
            a.eq(mux2("a_o", "a_oe", 0xFFFF_FFFF)),
            tt.eq(mux2("tt_o", "attr_oe", 0b11)),
            tm.eq(mux2("tm_o", "attr_oe", 0b111)),
            siz.eq(mux2("siz_o", "attr_oe", 0b11)),
            rw.eq(mux2("rw_o", "attr_oe", 1)),
            sc.eq(mux2("upa_o", "attr_oe", 0b00)),
            lock_n.eq(wand("lock_n_o", "attr_oe")),
            locke_n.eq(wand("locke_n_o", "attr_oe")),
            ciout_n.eq(wand("ciout_n_o", "attr_oe")),
            ts_n.eq(wand("ts_n_o", "ts_oe")),
            tip_n.eq(wand("tip_n_o", "tip_oe")),
            bb_n.eq(wand("bb_n_o", "bb_oe")),
        ]

        # The arbiter.
        self.arbiter = arb = M68040FairArbiter(use_locke=use_locke, gapless=gapless)
        self.comb += [
            arb.br_n[0].eq(c0.br_n_o), arb.br_n[1].eq(c1.br_n_o),
            arb.bb_n.eq(bb_n), arb.lock_n.eq(lock_n), arb.locke_n.eq(locke_n),
            c0.bg_n_i.eq(arb.bg_n[0]), c1.bg_n_i.eq(arb.bg_n[1]),
        ]
        # The grant the processors sampled on the last edge: a TS seen on this
        # edge was started on that one.
        who_q = Signal()
        self.sync += who_q.eq(arb.who)
        self.who = who_q

        # The MI gate: a processor's MI ignored from its reset to its first TS.
        mi_ignore = [Signal(reset=1, name="mi_ignore{}".format(i)) for i in range(2)]
        for c, ign in zip(cpus, mi_ignore):
            self.sync += If(~c.rsti_n, ign.eq(1)).Elif(c.ts_oe & ~c.ts_n_o, ign.eq(0))
        self.comb += self.mi_n.eq(reduce(and_, [
            (ign if mi_gate else 0) | c.mi_n_o for c, ign in zip(cpus, mi_ignore)]))

        # The bridge's terminations (the devices add TA and TCI, below).
        self.tea_n  = bus.tea_n
        self.tci_n  = Signal()
        self.tbi_n  = bus.tbi_n
        self.avec_n = bus.avec_n

        # The counters (STATS).
        self.stats = stats = {name: Signal(32, name="smp_" + name) for name in STATS}
        self.devices = dev = M68040SMPDevices(self, who_q, self.mi_n, stats)
        self.stats_logic(cpus, stats, dev.clear, who_q, arb)

        # D, and the terminations: a snooping processor may drive TA (it never
        # does in setup A), the devices TA and TCI; the bridge the rest.
        self.comb += [
            d.eq(Mux(c0.d_oe, c0.d_o, Mux(c1.d_oe, c1.d_o, Mux(dev.d_oe, dev.d_o, bus.d_r)))),
            ta_n.eq(bus.ta_n & dev.ta_n & wand("ta_n_o", "ta_oe")),
            # The bridge's view.
            bus.a.eq(a), bus.d_w.eq(d), bus.tt.eq(tt), bus.tm.eq(tm), bus.siz.eq(siz),
            bus.rw.eq(rw), bus.lock_n.eq(lock_n), bus.ciout_n.eq(ciout_n),
            bus.ts_n.eq(ts_n), bus.tip_n.eq(tip_n),
        ]
        self.comb  += self.tci_n.eq(bus.tci_n & dev.tci_n)

    def cpu_inputs(self):
        """rd68041_top's bussed inputs, the same for both processors: the
        resolved bus."""
        return dict(
            i_a_i     = self.a,      i_d_i    = self.d,      i_tt_i  = self.tt,
            i_rw_i    = self.rw,     i_siz_i  = self.siz,    i_ts_n_i = self.ts_n,
            i_ta_n_i  = self.ta_n,   i_tea_n_i = self.tea_n, i_tci_n_i = self.tci_n,
            i_tbi_n_i = self.tbi_n,  i_avec_n_i = self.avec_n,
            i_sc_i    = self.sc,     i_bb_n_i = self.bb_n,
        )

    def stats_logic(self, cpus, stats, clear, who, arb):
        c0, c1 = cpus
        inc = {name: Signal(name="inc_" + name) for name in STATS if name != "maxwait"}
        for name, s in inc.items():
            self.sync += If(clear, stats[name].eq(0)).Elif(s, stats[name].eq(stats[name] + 1))

        both = lambda oe: getattr(c0, oe) & getattr(c1, oe)
        self.comb += inc["contention"].eq(reduce(or_, [both(oe) for oe in
            ("a_oe", "attr_oe", "d_oe", "ts_oe", "tip_oe", "bb_oe", "ta_oe")]))
        # The address group: one processor drives it the clock after the other did.
        en      = [Signal(name="en{}".format(i)) for i in range(2)]
        en_prev = [Signal(name="en_prev{}".format(i)) for i in range(2)]
        for c, e, p in zip(cpus, en, en_prev):
            self.comb += e.eq(c.a_oe | c.attr_oe)
            self.sync += p.eq(e)
        self.comb += inc["turnaround"].eq((en[0] & ~en_prev[0] & en_prev[1]) |
                                          (en[1] & ~en_prev[1] & en_prev[0]))
        # Transfers, by whom; the owner check.
        ts     = Signal()
        driver = Signal()
        self.comb += [
            ts.eq(~self.ts_n),
            driver.eq(c1.ts_oe & ~c1.ts_n_o),
            inc["ts0"].eq(ts & ~driver),
            inc["ts1"].eq(ts & driver),
            inc["owner"].eq(ts & (driver != who)),
            inc["intervene"].eq(reduce(or_, [c.ta_oe & ~c.ta_n_o for c in cpus])),
            inc["miwait"].eq(~self.tip_n & ~self.mi_n),
        ]
        # Grants given.
        bg_prev = [Signal(reset=1, name="bg_prev{}".format(i)) for i in range(2)]
        for i in range(2):
            self.sync += bg_prev[i].eq(arb.bg_n[i])
        self.comb += inc["handovers"].eq(reduce(or_, [bg_prev[i] & ~arb.bg_n[i] for i in range(2)]))
        # Locked sequences: from a TS with LOCK to the termination of the
        # transfer with LOCKE; the other processor's TS in between breaks it.
        lk_on    = Signal()
        lk_owner = Signal()
        lk_end   = Signal()
        term     = Signal()
        self.comb += [
            term.eq(~self.ta_n | ~self.tea_n),
            inc["atomicity"].eq(ts & lk_on & (lk_owner != driver)),
            inc["locked"].eq(ts & ~self.lock_n & ~(lk_on & (lk_owner == driver))),
        ]
        self.sync += [
            If(ts,
                If(~self.lock_n,
                    lk_on.eq(1), lk_owner.eq(driver), lk_end.eq(~self.locke_n),
                ).Else(
                    lk_on.eq(0), lk_end.eq(0),
                ),
            ).Elif(lk_end & term & ~(~self.ta_n & ~self.tea_n),
                lk_on.eq(0), lk_end.eq(0),
            ),
        ]
        # The longest wait for a grant.
        waits = [Signal(32, name="wait{}".format(i)) for i in range(2)]
        for i, c in enumerate(cpus):
            self.sync += If(c.rsti_n & ~c.br_n_o & arb.bg_n[i],
                waits[i].eq(waits[i] + 1)).Else(waits[i].eq(0))
        self.sync += If(clear, stats["maxwait"].eq(0)).Else(
            *[If(waits[i] > stats["maxwait"], stats["maxwait"].eq(waits[i])) for i in range(2)])

# A processor on the board --------------------------------------------------------------------------

def rd68041_smp_params(pins, board, fpu, clk, rst_n, ipl_n):
    """rd68041_top's parameters for one processor of the board: its outputs to
    `pins` (m68040_pins_layout), its bussed inputs from the resolved bus
    (`board.cpu_inputs`), its own BG and RSTI from `pins`. `rst_n` is the
    core's asynchronous initialisation (RD68041 doc/pinout.md), `ipl_n` its
    IPL2-IPL0. AVEC comes with the bridge's TA on every acknowledge; DLE is
    low (normal mode); CDIS and MDIS negated; PCLK, PST and JTAG unused."""
    from litex.gen import Open
    return dict(
        p_FPU        = int(fpu),
        i_clk        = clk,
        i_pclk       = 0,
        i_rst_n      = rst_n,
        i_rsti_n_i   = pins.rsti_n,
        o_rsto_n_o   = Open(),
        o_a_o        = pins.a_o,      o_a_oe      = pins.a_oe,
        o_d_o        = pins.d_o,      o_d_oe      = pins.d_oe,
        o_tt_o       = pins.tt_o,     o_tm_o      = pins.tm_o,
        o_tln_o      = Open(),        o_upa_o     = pins.upa_o,
        o_rw_o       = pins.rw_o,     o_siz_o     = pins.siz_o,
        o_lock_n_o   = pins.lock_n_o, o_locke_n_o = pins.locke_n_o,
        o_ciout_n_o  = pins.ciout_n_o, o_attr_oe  = pins.attr_oe,
        o_ts_n_o     = pins.ts_n_o,   o_ts_oe     = pins.ts_oe,
        o_tip_n_o    = pins.tip_n_o,  o_tip_oe    = pins.tip_oe,
        o_ta_n_o     = pins.ta_n_o,   o_ta_oe     = pins.ta_oe,
        i_dle_i      = 0,
        o_mi_n_o     = pins.mi_n_o,
        o_br_n_o     = pins.br_n_o,   i_bg_n_i    = pins.bg_n_i,
        o_bb_n_o     = pins.bb_n_o,   o_bb_oe     = pins.bb_oe,
        i_cdis_n_i   = 1,
        i_mdis_n_i   = 1,
        i_ipl_n_i    = ipl_n,
        o_ipend_n_o  = Open(),
        o_pst_o      = Open(),
        i_tck_i      = 0,
        i_tms_i      = 1,
        i_tdi_i      = 1,
        i_trst_n_i   = 0,
        o_tdo_o      = Open(),
        o_tdo_oe     = Open(),
        **board.cpu_inputs(),
    )
