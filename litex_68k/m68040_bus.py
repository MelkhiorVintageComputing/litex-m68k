# SPDX-License-Identifier: BSD-2-Clause
# Copyright 2026 Romain Dolbeau

"""The MC68040 bus, and a slave on it that is the processor's side of a FIFO
bridge (m68k_fifo.py has the SoC's side and the FIFOs).

Written from the MC68040 User's Manual (UM), section 7, and not from any one
implementation of the processor: the same bridge is meant to sit behind real
pins with a real MC68040 on the other side. QuadraFPGA's 68040 slave
(Inputs/QuadraFPGA/quadra-to-ztex-gateware/mc68040_fsm.py), which works
against a real MC68040, is evidence of what the part does; where the two
differ the manual is followed (the order of a line's long words, below).

The MC68040's bus is synchronous (UM 7.4): every signal is driven and sampled
on the rising edge of BCLK. A transfer starts with TS for one clock, its
address and attributes stay on the bus until it ends, and each long word is
terminated by TA. There is no dynamic sizing: a transfer is a byte, a word or a
long word within one long word, or a line of four long words (UM 7.2). The
bridge runs on the processor's clock, rising edge only, and everything it
drives towards the processor comes from a register.
"""

from functools import reduce
from operator import or_

from migen import *
from migen.genlib.record import DIR_M_TO_S, DIR_S_TO_M

from litex.gen import *

from litex.soc.interconnect import stream

from litex_68k.m68k_fifo import TAG_BITS, request_layout, response_layout, M68kFIFOBridge

# The bus ------------------------------------------------------------------------------------------

# The signals of UM section 5 that a transfer uses, named from the processor's
# side: M_TO_S are the processor's outputs, S_TO_M its inputs. Active-low signals
# keep their _n. The three-state pins are already split: d_w is what the
# processor drives on D31-D0, d_r what it reads there. The processor is the
# only master: no arbitration and no snooping here.
m68040_bus_layout = [
    ("a",       32, DIR_M_TO_S), # address (UM 5.1)
    ("d_w",     32, DIR_M_TO_S), # data, write (UM 5.2)
    ("d_r",     32, DIR_S_TO_M), # data, read
    ("tt",       2, DIR_M_TO_S), # transfer type (UM 5.3.1, Table 5-2); 3 is an acknowledge
    ("tm",       3, DIR_M_TO_S), # transfer modifier (UM 5.3.2); an acknowledge's level
    ("siz",      2, DIR_M_TO_S), # 0 long word, 1 byte, 2 word, 3 line (UM 5.3.6)
    ("rw",       1, DIR_M_TO_S), # 1 = read (UM 5.3.5)
    ("lock_n",   1, DIR_M_TO_S), # locked transfer (UM 5.3.7)
    ("ciout_n",  1, DIR_M_TO_S), # cache inhibit out (UM 5.3.9)
    ("ts_n",     1, DIR_M_TO_S), # transfer start, one clock (UM 5.4.1)
    ("tip_n",    1, DIR_M_TO_S), # transfer in progress (UM 5.4.2)
    ("ta_n",     1, DIR_S_TO_M), # transfer acknowledge (UM 5.4.3)
    ("tea_n",    1, DIR_S_TO_M), # transfer error acknowledge (UM 5.4.4); with TA, retry
    ("tci_n",    1, DIR_S_TO_M), # transfer cache inhibit (UM 5.4.5)
    ("tbi_n",    1, DIR_S_TO_M), # transfer burst inhibit (UM 5.4.6)
    ("avec_n",   1, DIR_S_TO_M), # autovector, with TA on an interrupt acknowledge (UM 5.8.3)
]

SIZ_LONG, SIZ_BYTE, SIZ_WORD, SIZ_LINE = 0, 1, 2, 3
TT_ACK = 3

def m68040_byte_enables(siz, a):
    """The byte lanes of a transfer (UM Table 7-1). An operand is always within
    one long word (the processor splits misaligned ones, UM 7.3): a byte at
    A1-A0, a word at A1, a long word or a line on all four. The byte at offset k
    of the long word is on D(31-8k):D(24-8k), so its byte enable is sel[3-k]:
    the bus is big-endian on both sides and nothing is swapped."""
    sel = Signal(4)
    return sel, [
        Case(siz, {
            SIZ_BYTE:  sel.eq(0b1000 >> a[0:2]),
            SIZ_WORD:  sel.eq(Mux(a[1], 0b0011, 0b1100)),
            "default": sel.eq(0b1111),
        }),
    ]

# The processor's side -----------------------------------------------------------------------------

class M68040ToFIFO(LiteXModule):
    """An MC68040 bus slave that makes each transfer a request on `request`
    (m68k_fifo's wide format) and ends it on its answer from `response`, or at
    once, for a posted write.

    - TS is remembered when it is seen, whatever the bridge is doing: it lasts
      one clock, and the processor may assert it on the very edge it samples
      the last TA of the transfer before (UM 7.4.1). The address and the
      attributes are then read from the bus, where they stay until the
      transfer's last TA (UM 7.4.1, 7.4.2).
    - Reads wait for their answer. A line read (SIZ = 3: a cache line fill, or
      MOVE16) gets the whole line, handed out in a burst of four long words,
      one a clock, TA on each: first the one at the transfer's A3-A2, then the
      others with A3-A2 incremented, wrapping (UM 7.4.2: "the selected device
      must internally increment A3 and A2"). QuadraFPGA hands a line out in
      the order 0-3 whatever A3-A2 is; that is not what the manual asks for.
    - TCI is asserted with the first TA of every read in io_regions (UM 5.4.5),
      so that no cache keeps a device register. A line read there still reads
      the line's four long words (TCI does not stop a line, UM 5.4.5); software
      keeps the processor from making one, by a transparent translation
      register or a page's cache mode (noncachable, serialized).
    - Writes outside io_regions are posted: TA as soon as the request is in the
      FIFO, and a write the SoC cannot do is lost, since the processor has moved
      on. A line write (a copyback push, or MOVE16) is taken a long word a
      clock, TA on each (UM 7.4.4), then posted as one request, wherever it is.
      A long word, word or byte write in io_regions waits for its answer, so a
      device register is written before the next instruction runs, and an error
      there is an error on that transfer.
    - An error is TEA alone, on the transfer's first long word (UM 7.6.1: a
      line stops there). TA with TEA (retry, UM 7.6.2) is never used, nor TBI:
      every line is burst.
    - Acknowledge transfers (TT = 3, UM 7.5) are not forwarded: an interrupt
      acknowledge (TM = the level, A = $FFFFFFFF) gets AVEC with TA (UM
      7.5.1.2); a breakpoint acknowledge (TM = 0) TEA, which makes the
      processor take an illegal instruction exception (UM 7.5.2).
    """
    def __init__(self, bus, io_regions={}):
        self.bus      = bus
        self.request  = request  = stream.Endpoint(request_layout(wide=True))
        self.response = response = stream.Endpoint(response_layout())

        # # #

        # What the bridge drives towards the processor, all from registers.
        d_r    = Signal(32)
        ta_n   = Signal(reset=1)
        tea_n  = Signal(reset=1)
        tci_n  = Signal(reset=1)
        avec_n = Signal(reset=1)
        self.comb += [
            bus.d_r.eq(d_r),
            bus.ta_n.eq(ta_n),
            bus.tea_n.eq(tea_n),
            bus.tci_n.eq(tci_n),
            bus.avec_n.eq(avec_n),
            bus.tbi_n.eq(1),        # Every line is burst.
        ]

        # A transfer started (TS seen) that the FSM has not taken yet.
        ts      = Signal()
        pending = Signal()
        take    = Signal()
        self.comb += ts.eq(~bus.ts_n | pending)
        self.sync += pending.eq(Mux(take, 0, ts))

        # Decoding, from the bus (stable from TS to the last TA).
        ack   = Signal()
        line  = Signal()
        in_io = Signal()
        self.comb += [
            ack.eq(bus.tt == TT_ACK),
            line.eq(bus.siz == SIZ_LINE),
            in_io.eq(reduce(or_, [(bus.a >= origin) & (bus.a <= origin + length - 1)
                                  for origin, length in io_regions.items()], 0)),
        ]
        write_sel, write_sel_comb = m68040_byte_enables(bus.siz, bus.a)
        self.comb += write_sel_comb

        # The transfer, as taken.
        adr   = Signal(30)
        io    = Signal()
        burst = Signal()            # A line read: answer with a burst.
        tag   = Signal(TAG_BITS)
        rline = Signal(128)         # The line read.
        wline = Signal(128)         # The line written.
        word  = Signal(2)           # The long word of the line that goes with the next TA.
        beat  = Signal(2)
        self.comb += request.tag.eq(tag)
        rwords = Array(rline[32*k:32*(k + 1)] for k in range(4))

        # A single write: its long word in all four words of the line, its byte
        # enables at its own (m68k_fifo: the wide format).
        single_write = [
            request.we.eq(1),
            request.adr.eq(adr),
            request.line.eq(0),
            request.answer.eq(io),
            request.sel.eq(write_sel << Cat(Constant(0, 2), adr[0:2])),
        ]

        self.fsm = fsm = FSM(reset_state="IDLE")
        # A read is requested right away, from the bus.
        fsm.act("IDLE",
            NextValue(ta_n,   1),
            NextValue(tea_n,  1),
            NextValue(tci_n,  1),
            NextValue(avec_n, 1),
            request.we.eq(0),
            request.adr.eq(bus.a[2:]),
            request.line.eq(line),
            request.answer.eq(1),
            If(ts,
                take.eq(1),
                NextValue(adr, bus.a[2:]),
                NextValue(io, in_io),
                NextValue(burst, line),
                NextValue(word, bus.a[2:4]),
                NextValue(beat, 0),
                If(ack,
                    If(bus.tm != 0,
                        NextValue(avec_n, 0),
                        NextValue(ta_n, 0),
                    ).Else(
                        NextValue(tea_n, 0),
                    ),
                ).Elif(bus.rw,
                    request.valid.eq(1),
                    If(request.ready,
                        NextState("WAIT"),
                    ).Else(
                        NextState("READ-REQUEST"),
                    ),
                ).Elif(line,
                    # The first long word is on the bus by the end of C2 (UM
                    # 7.4.4), so TA may go with it: one each clock from here.
                    NextValue(ta_n, 0),
                    NextState("LINE-WRITE"),
                ).Else(
                    NextState("WRITE"),
                ),
            ),
        )
        fsm.act("READ-REQUEST",
            request.valid.eq(1),
            request.we.eq(0),
            request.adr.eq(adr),
            request.line.eq(burst),
            request.answer.eq(1),
            If(request.ready,
                NextState("WAIT"),
            ),
        )
        # A single write: its data is on the bus from C2 (UM 7.4.3), which this
        # state is at the earliest, until its TA.
        fsm.act("WRITE",
            single_write,
            request.dat.eq(Replicate(bus.d_w, 4)),
            request.valid.eq(1),
            NextValue(wline, Replicate(bus.d_w, 4)),
            If(request.ready,
                If(io,
                    NextState("WAIT"),
                ).Else(
                    NextValue(ta_n, 0),
                    NextState("IDLE"),
                ),
            ).Else(
                NextState("WRITE-REQUEST"),
            ),
        )
        fsm.act("WRITE-REQUEST",
            single_write,
            request.dat.eq(wline),
            request.valid.eq(1),
            If(request.ready,
                If(io,
                    NextState("WAIT"),
                ).Else(
                    NextValue(ta_n, 0),
                    NextState("IDLE"),
                ),
            ),
        )
        # A line write: each long word is taken on the edge the processor
        # samples its TA, and the next is on the bus from then on (UM 7.4.4,
        # Figure 7-17). The fourth: TA negated, the line posted.
        self.sync += If(fsm.ongoing("LINE-WRITE"),
            Case(word, {k: wline[32*k:32*(k + 1)].eq(bus.d_w) for k in range(4)}),
        )
        fsm.act("LINE-WRITE",
            NextValue(word, word + 1),
            NextValue(beat, beat + 1),
            If(beat == 3,
                NextValue(ta_n, 1),
                NextState("LINE-WRITE-REQUEST"),
            ),
        )
        fsm.act("LINE-WRITE-REQUEST",
            request.valid.eq(1),
            request.we.eq(1),
            request.adr.eq(adr),
            request.line.eq(1),
            request.answer.eq(0),
            request.dat.eq(wline),
            request.sel.eq(0xffff),
            If(request.ready,
                NextState("IDLE"),
            ),
        )
        # The answer. One with another tag is for a transfer this side has given
        # up (its reset): it is dropped.
        fsm.act("WAIT",
            response.ready.eq(1),
            If(response.valid & (response.tag == tag),
                NextValue(tag, tag + 1),
                NextValue(rline, response.dat),
                NextValue(d_r, Array(response.dat[32*k:32*(k + 1)] for k in range(4))[word]),
                NextValue(word, word + 1),
                If(response.err,
                    NextValue(tea_n, 0),
                    NextState("IDLE"),
                ).Else(
                    NextValue(ta_n, 0),
                    NextValue(tci_n, ~io),
                    If(burst,
                        NextValue(beat, 1),
                        NextState("BURST"),
                    ).Else(
                        NextState("IDLE"),
                    ),
                ),
            ),
        )
        # The line's other three long words, TA held: each is put on the bus on
        # the edge the processor samples the one before (UM 7.4.2, Figure 7-11).
        fsm.act("BURST",
            NextValue(tci_n, 1),
            NextValue(d_r, rwords[word]),
            NextValue(word, word + 1),
            NextValue(beat, beat + 1),
            If(beat == 3,
                NextState("IDLE"),
            ),
        )

# The two, and the FIFOs ---------------------------------------------------------------------------

class M68040ToFIFOToSoC(M68kFIFOBridge):
    """M68040ToFIFO in clock domain `cd`, FIFOToSoC (wide) in sys, and the FIFOs
    between them (M68kFIFOBridge, m68k_fifo.py: its reset). `bus` is the
    MC68040 bus, `wishbone_bus` the SoC's side Wishbone master (in sys)."""
    def __init__(self, bus, wishbone_bus, cd="sys", io_regions={}, depth=8):
        M68kFIFOBridge.__init__(self,
            M68040ToFIFO(bus, io_regions=io_regions),
            wishbone_bus, cd=cd, wide=True, depth=depth)
