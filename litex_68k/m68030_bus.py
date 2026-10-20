# SPDX-License-Identifier: BSD-2-Clause
# Copyright 2026 Romain Dolbeau

"""The MC68030 bus, and a slave on it that is a Wishbone master.

Written from the MC68030 User's Manual (UM), section 7, and not from any one
implementation of the processor: the same bridge is meant to sit behind real
pins with a real MC68030 on the other side. Its starting point is the PDS
bridge of IIsiFPGA (Inputs/IIsiFPGA/IIsi-to-ztex-gateware/mc68030_fsm.py),
which works against a real MC68030.

The bridge is a 32-bit port that terminates every cycle synchronously (STERM,
UM 7.3.4-7.3.5), so the processor never has to size the port dynamically. It
runs on the processor's clock, rising edge only, and everything it drives
towards the processor comes from a register.
"""

from functools import reduce
from operator import or_

from migen import *
from migen.genlib.record import DIR_M_TO_S, DIR_S_TO_M

from litex.gen import *

from litex.soc.interconnect import wishbone

# The bus ------------------------------------------------------------------------------------------

# The signals of UM section 5 that a bus cycle uses, named from the processor's
# side: M_TO_S are the processor's outputs, S_TO_M its inputs. Active-low signals
# keep their _n. The three-state pins are already split: d_w is what the
# processor drives on D31-D0, d_r what it reads there.
m68030_bus_layout = [
    ("fc",       3, DIR_M_TO_S), # function codes (UM 5.2); 7 is CPU space
    ("a",       32, DIR_M_TO_S), # address (UM 5.3)
    ("d_w",     32, DIR_M_TO_S), # data, write (UM 5.4)
    ("d_r",     32, DIR_S_TO_M), # data, read
    ("siz",      2, DIR_M_TO_S), # bytes remaining, 0 = 4 (UM 5.5, Table 7-2)
    ("rw",       1, DIR_M_TO_S), # 1 = read (UM 5.6.3)
    ("as_n",     1, DIR_M_TO_S), # address strobe (UM 5.6.5)
    ("ds_n",     1, DIR_M_TO_S), # data strobe (UM 5.6.6)
    ("rmc_n",    1, DIR_M_TO_S), # read-modify-write cycle (UM 5.6.4)
    ("ciout_n",  1, DIR_M_TO_S), # cache inhibit out (UM 5.7.2)
    ("cbreq_n",  1, DIR_M_TO_S), # cache burst request (UM 5.7.3)
    ("dsack_n",  2, DIR_S_TO_M), # asynchronous termination and port size (UM 5.6.8)
    ("sterm_n",  1, DIR_S_TO_M), # synchronous termination, 32-bit port (UM 5.6.9)
    ("ciin_n",   1, DIR_S_TO_M), # cache inhibit in (UM 5.7.1)
    ("cback_n",  1, DIR_S_TO_M), # cache burst acknowledge (UM 5.7.4)
    ("avec_n",   1, DIR_S_TO_M), # autovector (UM 5.8.3)
    ("berr_n",   1, DIR_S_TO_M), # bus error (UM 5.10.1)
    ("halt_n",   1, DIR_S_TO_M), # halt (UM 5.10.3)
]

def m68030_byte_enables(siz, a):
    """The byte lanes a 32-bit port takes in a write (UM Table 7-5).

    The processor moves SIZ bytes (0 means 4) starting at A1-A0, as many as fit
    before the next long-word boundary; the rest goes in the next cycle (UM
    7.2.3). The byte at offset k of the long word is on D(31-8k):D(24-8k), so its
    Wishbone byte enable is sel[3-k]: the bus is big-endian on both sides and
    nothing is swapped.
    """
    n = Signal(3)
    sel = Signal(4)
    return n, sel, [
        n.eq(Mux(siz == 0, 4, siz)),
        sel.eq(Cat(*[(a[0:2] <= k) & (a[0:2] + n > k) for k in reversed(range(4))])),
    ]

def m68030_coprocessor_cycle(bus, cpids):
    """True when the cycle on `bus` is to a coprocessor whose CpID is in `cpids`:
    CPU space, A19-A16 = 2 (coprocessor communication), A15-A13 = CpID (UM 10.1,
    Figure 10-2)."""
    return reduce(or_, [(bus.fc == 0b111) & (bus.a[16:20] == 0x2) & (bus.a[13:16] == cpid)
                        for cpid in cpids], 0)

# The bridge ---------------------------------------------------------------------------------------

class M68030ToWishbone(LiteXModule):
    """An MC68030 bus slave, terminating every cycle with STERM, that forwards
    each cycle outside CPU space to a Wishbone master.

    - Memory cycles (FC != 7) become one Wishbone cycle each. A read takes the
      whole long word, sel = 0xF: the caches load whole long words (UM Table 7-4,
      note), and a cachable read needs them all. A write takes the lanes of
      Table 7-5. The cycle ends with STERM for one clock; read data is held until
      the next read.
    - CIIN is asserted with STERM for an address in io_regions (UM 7.3.4), so
      neither cache keeps a device register.
    - A Wishbone error, or no answer within `timeout` clocks, is a bus error:
      BERR, held until AS is negated (UM 7.5.1, Table 7-9). timeout=None: only
      a Wishbone error, for a bus that has its own time-out and must not be
      left mid-cycle (behind a clock domain crossing, which would still be
      waiting for the answer when the next cycle comes).
    - CPU space (FC = 7, UM 7.4): an interrupt acknowledge (A19-A16 = $F) is
      autovectored, AVEC held until AS is negated (UM 7.4.1.2). Every other CPU
      space cycle gets BERR: a breakpoint acknowledge then takes the illegal
      instruction exception (UM 7.4.2), and a coprocessor access with no
      coprocessor to answer it the F-line exception (UM 10.1.1). A cycle to a
      coprocessor in `coprocessors` (CpIDs) is left alone: that coprocessor is
      on the bus too, and answers it with DSACK.

    Timing, rising edges only (the processor's own rising edges, in its clock
    domain): AS is asserted in S1, on a falling edge, and is seen on the rising
    edge that starts S2. A write's data is driven from S2 (UM 7.3.2), so it is
    taken one clock later. STERM is sampled on rising edges and the read data
    latched on the following falling edge (UM 7.3.4); this bridge drives both
    from registers, so it has a full clock to meet STERM and CIIN and half a
    clock for the data, AVEC and BERR, which are sampled on falling edges.
    """
    def __init__(self, bus=None, wishbone_bus=None, io_regions={}, timeout=1024, coprocessors=()):
        self.bus      = bus = Record(m68030_bus_layout) if bus is None else bus
        self.wishbone = wb  = wishbone.Interface(data_width=32, address_width=32, addressing="word") \
                              if wishbone_bus is None else wishbone_bus
        assert wb.data_width == 32 and wb.addressing == "word"

        # # #

        # What the bridge drives towards the processor, all from registers.
        d_r     = Signal(32)
        sterm_n = Signal(reset=1)
        ciin_n  = Signal(reset=1)
        avec_n  = Signal(reset=1)
        berr_n  = Signal(reset=1)
        self.comb += [
            bus.d_r.eq(d_r),
            bus.sterm_n.eq(sterm_n),
            bus.ciin_n.eq(ciin_n),
            bus.avec_n.eq(avec_n),
            bus.berr_n.eq(berr_n),
            bus.dsack_n.eq(0b11), # Never asynchronous...
            bus.cback_n.eq(1),    # ... nor burst ...
            bus.halt_n.eq(1),     # ... nor retried.
        ]

        # Decoding, from the address phase (valid from S0, UM 7.3.1).
        cpu_space = Signal()
        iack      = Signal()
        coprocessor = Signal()
        in_io     = Signal()
        self.comb += [
            cpu_space.eq(bus.fc == 0b111),
            iack.eq(bus.a[16:20] == 0xf),
            coprocessor.eq(m68030_coprocessor_cycle(bus, coprocessors)),
            in_io.eq(reduce(or_, [(bus.a >= origin) & (bus.a <= origin + length - 1)
                                  for origin, length in io_regions.items()], 0)),
        ]
        _, write_sel, write_sel_comb = m68030_byte_enables(bus.siz, bus.a)
        self.comb += write_sel_comb

        # The Wishbone cycle, from registers taken in the address phase.
        adr   = Signal(30)
        sel   = Signal(4)
        we    = Signal()
        dat_w = Signal(32)
        io    = Signal()
        count = Signal(max=(timeout or 1) + 1)
        self.comb += [
            wb.adr.eq(adr),
            wb.sel.eq(sel),
            wb.we.eq(we),
            wb.dat_w.eq(dat_w),
        ]

        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            If(~bus.as_n,
                If(cpu_space,
                    If(iack,
                        NextValue(avec_n, 0),
                        NextState("END"),
                    ).Elif(coprocessor,
                        NextState("ELSEWHERE"),
                    ).Else(
                        NextValue(berr_n, 0),
                        NextState("END"),
                    ),
                ).Else(
                    NextValue(adr, bus.a[2:]),
                    NextValue(sel, Mux(bus.rw, 0b1111, write_sel)),
                    NextValue(we, ~bus.rw),
                    NextValue(io, in_io),
                    NextValue(count, 0),
                    If(bus.rw,
                        NextState("WISHBONE"),
                    ).Else(
                        NextState("WRITE-DATA"),
                    ),
                ),
            ),
        )
        fsm.act("WRITE-DATA",
            NextValue(dat_w, bus.d_w),
            NextState("WISHBONE"),
        )
        fsm.act("WISHBONE",
            wb.cyc.eq(1),
            wb.stb.eq(1),
            NextValue(count, count + 1),
            If(wb.ack,
                If(~we,
                    NextValue(d_r, wb.dat_r),
                ),
                NextValue(ciin_n, ~io),
                NextValue(sterm_n, 0),
                NextState("END"),
            ).Elif(wb.err | ((count == timeout - 1) if timeout else 0),
                NextValue(berr_n, 0),
                NextState("END"),
            ),
        )
        # A cycle another slave on the bus answers (a coprocessor's, UM 10.1).
        fsm.act("ELSEWHERE",
            If(bus.as_n,
                NextState("IDLE"),
            ),
        )
        # STERM is one clock (UM 7.3.4: negated within two). AVEC and BERR are held
        # until the processor negates AS. Waiting for AS to go away costs nothing:
        # the next cycle's AS cannot be seen before the edge after that.
        fsm.act("END",
            NextValue(sterm_n, 1),
            If(bus.as_n,
                NextValue(avec_n, 1),
                NextValue(berr_n, 1),
                NextState("IDLE"),
            ),
        )
