# SPDX-License-Identifier: BSD-2-Clause
# Copyright 2026 Romain Dolbeau

"""An MC68030 bus slave and the SoC's bus master, joined by two FIFOs.

The processor's side (M68030ToFIFO) runs on the processor's clock and the
SoC's side (FIFOToSoC) on sys; between them, two asynchronous FIFOs, so the
two clocks need not be related at all: the processor's may be any frequency,
and come from a pin, for a real MC68030 with its own oscillator.

- Requests, processor to SoC: read or write, word address, data and byte
  enables of a write, whether a read is for a whole cache line, whether a
  write wants an answer, and a tag.
- Answers, SoC to processor: a cache line (128 bits, word k of the line, at
  byte offset 4k, in bits 32k+31..32k), an error bit and the tag of the request.

Writes outside io_regions are posted: the processor's cycle ends as soon as
the request is in the FIFO, and a write the SoC cannot do is lost, since the
processor has moved on. Writes in io_regions, and reads, wait for their
answer, so a bus error there is a bus error on that cycle (UM 7.5.1), and a
device register is written before the next instruction runs. Requests are
carried out in order, so a read always sees the writes before it.

A cachable read for which the processor asks for a burst (CBREQ, UM 7.3.7)
gets a whole line, which the processor's side hands out in a burst, one long
word per clock. On the SoC's side, a LiteDRAM native port as wide as a line
(the Wukong's DDR3: 16 bits, 1:4, 128 bits) answers every read with a line in
one access; Wishbone needs four.
"""

from functools import reduce
from operator import or_

from migen import *

from litex.gen import *

from litex.soc.interconnect import stream, wishbone

from litex_68k.m68030_bus import m68030_byte_enables, m68030_coprocessor_cycle

TAG_BITS = 3

def request_layout():
    return [
        ("we",       1),  # 1: write.
        ("adr",     30),  # Word address.
        ("dat",     32),  # Write data, big-endian (byte 0 in 31..24, as on D31-D0).
        ("sel",      4),  # Write byte enables (sel[3]: byte 0).
        ("line",     1),  # Read: the whole line (adr[0:2] is the word wanted first).
        ("answer",   1),  # Write: answer it (reads are always answered).
        ("tag", TAG_BITS),
    ]

def response_layout():
    return [
        ("dat",    128),  # The line: word k in bits 32k+31..32k. A single word in all four.
        ("err",      1),  # Bus error.
        ("tag", TAG_BITS),
    ]

# The processor's side -----------------------------------------------------------------------------

class M68030ToFIFO(LiteXModule):
    """An MC68030 bus slave that makes each memory cycle a request on `request`
    and ends it on its answer from `response` (or at once, for a posted write).

    Terminations are synchronous (STERM, UM 7.3.4-7.3.5), all signals towards the
    processor from registers, rising edges only. STERM is sampled on a rising
    edge and the data latched on the following falling edge (UM 7.3.4), so the
    data goes with STERM for one long word, but in a burst each next long word
    is put on the bus on the edge where the processor samples the STERM of the
    one before it, and is latched half a clock later:

        edge     E0       E1       E2       E3       E4
        STERM    on       on       on       on       off
        CBACK    on       on       on       off      off
        D        word 0   word 0   word 1   word 2   word 3
        sampled           STERM 0  STERM 1  STERM 2  STERM 3 (falling edge after: word 3)

    "word 0" is the long word the processor asked for, the others follow in
    the line, wrapping (A3-A2 incremented by the system hardware, UM 7.3.7).
    CBACK sampled with the fourth STERM says nothing more is coming.

    CPU space (FC = 7) is never forwarded: AVEC for an interrupt acknowledge,
    nothing for a coprocessor in `coprocessors` (CpIDs: it is on the bus and
    answers), BERR for the rest (as M68030ToWishbone, see there).
    """
    def __init__(self, bus, io_regions={}, coprocessors=()):
        self.bus      = bus
        self.request  = request  = stream.Endpoint(request_layout())
        self.response = response = stream.Endpoint(response_layout())

        # # #

        # What the bridge drives towards the processor, all from registers.
        d_r     = Signal(32)
        sterm_n = Signal(reset=1)
        ciin_n  = Signal(reset=1)
        cback_n = Signal(reset=1)
        avec_n  = Signal(reset=1)
        berr_n  = Signal(reset=1)
        self.comb += [
            bus.d_r.eq(d_r),
            bus.sterm_n.eq(sterm_n),
            bus.ciin_n.eq(ciin_n),
            bus.cback_n.eq(cback_n),
            bus.avec_n.eq(avec_n),
            bus.berr_n.eq(berr_n),
            bus.dsack_n.eq(0b11), # Never asynchronous...
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

        # The cycle, as taken in the address phase.
        adr   = Signal(30)
        sel   = Signal(4)
        dat_w = Signal(32)
        io    = Signal()
        burst = Signal()    # A cachable read with CBREQ: answer with a burst.
        tag   = Signal(TAG_BITS)
        line  = Signal(128)
        word  = Signal(2)   # The next long word of `line` to put on the bus.
        beat  = Signal(2)
        self.comb += [
            request.tag.eq(tag),
            request.sel.eq(sel),
            request.dat.eq(dat_w),
        ]
        words = Array(line[32*k:32*(k + 1)] for k in range(4))

        self.fsm = fsm = FSM(reset_state="IDLE")
        # A read is requested right away, from the bus; a write once its data is
        # there (UM 7.3.2: from S2), from registers.
        fsm.act("IDLE",
            request.we.eq(0),
            request.adr.eq(bus.a[2:]),
            request.line.eq(~in_io & ~bus.cbreq_n),
            request.answer.eq(1),
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
                    NextValue(sel, write_sel),
                    NextValue(io, in_io),
                    NextValue(burst, ~in_io & ~bus.cbreq_n),
                    NextValue(word, bus.a[2:4]),
                    If(bus.rw,
                        request.valid.eq(1),
                        If(request.ready,
                            NextState("WAIT"),
                        ).Else(
                            NextState("READ-REQUEST"),
                        ),
                    ).Else(
                        NextState("WRITE-DATA"),
                    ),
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
        fsm.act("WRITE-DATA",
            NextValue(dat_w, bus.d_w),
            NextState("WRITE-REQUEST"),
        )
        fsm.act("WRITE-REQUEST",
            request.valid.eq(1),
            request.we.eq(1),
            request.adr.eq(adr),
            request.line.eq(0),
            request.answer.eq(io),
            If(request.ready,
                If(io,
                    NextState("WAIT"),
                ).Else(
                    NextValue(sterm_n, 0),
                    NextState("END"),
                ),
            ),
        )
        # The answer. One with another tag is for a cycle this side has given up
        # (its reset): it is dropped.
        fsm.act("WAIT",
            response.ready.eq(1),
            If(response.valid & (response.tag == tag),
                NextValue(tag, tag + 1),
                NextValue(line, response.dat),
                NextValue(d_r, Array(response.dat[32*k:32*(k + 1)] for k in range(4))[word]),
                NextValue(word, word + 1),
                If(response.err,
                    NextValue(berr_n, 0),
                    NextState("END"),
                ).Else(
                    NextValue(sterm_n, 0),
                    NextValue(ciin_n, ~io),
                    If(burst,
                        NextValue(cback_n, 0),
                        NextValue(beat, 0),
                        NextState("BURST"),
                    ).Else(
                        NextState("END"),
                    ),
                ),
            ),
        )
        # Edges E1-E4 of the table above. Should the processor end the cycle
        # anyway, AS negated, STERM goes at once.
        fsm.act("BURST",
            NextValue(beat, beat + 1),
            If(bus.as_n | ((beat == 0) & bus.cbreq_n),
                NextValue(sterm_n, 1),
                NextValue(cback_n, 1),
                NextState("END"),
            ).Else(
                If(beat != 0,
                    NextValue(d_r, words[word]),
                    NextValue(word, word + 1),
                ),
                If(beat == 2,
                    NextValue(cback_n, 1),
                ),
                If(beat == 3,
                    NextValue(sterm_n, 1),
                    NextState("END"),
                ),
            ),
        )
        # A cycle another slave on the bus answers (a coprocessor's, UM 10.1).
        fsm.act("ELSEWHERE",
            If(bus.as_n,
                NextState("IDLE"),
            ),
        )
        # STERM is one clock (UM 7.3.4: negated within two). AVEC and BERR are held
        # until the processor negates AS.
        fsm.act("END",
            NextValue(sterm_n, 1),
            NextValue(cback_n, 1),
            If(bus.as_n,
                NextValue(avec_n, 1),
                NextValue(berr_n, 1),
                NextState("IDLE"),
            ),
        )

# The SoC's side -----------------------------------------------------------------------------------

class FIFOToSoC(LiteXModule):
    """Carries out the requests from `request`, in order, one at a time, as a
    Wishbone master, or on a LiteDRAM native port for main RAM
    (add_native_port), and answers on `response` the reads and the writes that
    want it.

    Wishbone: a line read is four accesses, a word read one, its long word in
    all four words of the answer. An error, or no answer from the bus, is an
    error in the answer; a time-out is the bus's business (WishboneErrorTimeout
    in core.py), as a Wishbone master cannot end an access by itself.

    Native port: as wide as a line. Every read reads the line; a write writes its
    long word in it."""
    def __init__(self, wishbone_bus):
        self.request  = stream.Endpoint(request_layout())
        self.response = stream.Endpoint(response_layout())
        self.wishbone = wishbone_bus
        assert wishbone_bus.data_width == 32 and wishbone_bus.addressing == "word"
        self.port     = None

    def add_native_port(self, port, origin, size):
        assert len(port.wdata.data) == 128, "the native port must be as wide as a line"
        assert origin % 16 == 0 and size % 16 == 0
        self.port, self.port_origin, self.port_size = port, origin, size

    def do_finalize(self):
        req, rsp, wb, port = self.request, self.response, self.wishbone, self.port

        # The request being carried out, taken from its FIFO at once: the
        # processor's side may then go on with the next (a posted write), and
        # without FIFOs (both sides in sys), it waits for its request to be
        # taken before it waits for the answer.
        r    = Record(request_layout())
        line = Signal(128)
        err  = Signal()
        n    = Signal(2)    # Wishbone: the word of the line.
        self.comb += [
            rsp.dat.eq(line),
            rsp.err.eq(err),
            rsp.tag.eq(r.tag),
        ]

        native = Signal()   # Of req, as it is taken.
        if port is not None:
            byte_adr = Cat(Constant(0, 2), req.adr)
            self.comb += [
                native.eq((byte_adr >= self.port_origin) & (byte_adr < self.port_origin + self.port_size)),
                port.cmd.addr.eq(r.adr[2:] - (self.port_origin >> 4)),
                port.cmd.last.eq(1),
                port.wdata.data.eq(Replicate(r.dat, 4)),
                port.wdata.we.eq(r.sel << Cat(Constant(0, 2), r.adr[0:2])),
            ]

        self.comb += [
            wb.adr.eq(Mux(r.line, Cat(n, r.adr[2:]), r.adr)),
            wb.we.eq(r.we),
            wb.sel.eq(Mux(r.we, r.sel, 0b1111)),
            wb.dat_w.eq(r.dat),
        ]

        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            NextValue(err, 0),
            NextValue(n, 0),
            req.ready.eq(1),
            If(req.valid,
                [NextValue(getattr(r, name), getattr(req, name)) for name, _ in request_layout()],
                NextState("WISHBONE"),
                If(native,
                    NextState("NATIVE-CMD"),
                ) if port is not None else [],
            ),
        )
        fsm.act("WISHBONE",
            wb.cyc.eq(1),
            wb.stb.eq(1),
            If(wb.ack | wb.err,
                NextValue(err, err | wb.err),
                If(r.line,
                    # In order: after the fourth, word k is in bits 32k+31..32k.
                    NextValue(line, Cat(line[32:], wb.dat_r)),
                ).Else(
                    NextValue(line, Replicate(wb.dat_r, 4)),
                ),
                NextValue(n, n + 1),
                If(~r.line | (n == 3),
                    NextState("RESPOND"),
                ),
            ),
        )
        if port is not None:
            fsm.act("NATIVE-CMD",
                port.cmd.valid.eq(1),
                port.cmd.we.eq(r.we),
                If(port.cmd.ready,
                    If(r.we,
                        NextState("NATIVE-WDATA"),
                    ).Else(
                        NextState("NATIVE-RDATA"),
                    ),
                ),
            )
            fsm.act("NATIVE-WDATA",
                port.wdata.valid.eq(1),
                If(port.wdata.ready,
                    NextState("RESPOND"),
                ),
            )
            fsm.act("NATIVE-RDATA",
                port.rdata.ready.eq(1),
                If(port.rdata.valid,
                    NextValue(line, port.rdata.data),
                    NextState("RESPOND"),
                ),
            )
        # Done: the answer, if the request wants one.
        answer = Signal()
        self.comb += answer.eq(~r.we | r.answer)
        fsm.act("RESPOND",
            rsp.valid.eq(answer),
            If(~answer | rsp.ready,
                NextState("IDLE"),
            ),
        )

# The two, and the FIFOs ---------------------------------------------------------------------------

class M68030ToFIFOToSoC(LiteXModule):
    """M68030ToFIFO in clock domain `cd`, FIFOToSoC in sys, and the FIFOs between
    them (none when cd is sys). `bus` is the MC68030 bus, `wishbone_bus` the
    SoC's side Wishbone master (in sys).

    Reset: all of it, on both sides, while either clock domain is in reset.
    LiteX's FIFOs reset their pointers synchronously, so one side resets only
    once its clock runs. Two clocks from two MMCMs come out of reset at
    different times, and a side out of reset first would read the other
    side's pointer as it was before the reset: a FIFO with stale entries in
    it, then pointers that disagree for good. (Seen on the Wukong: `reboot`,
    junk on the UART, then nothing.) Each side's reset is its own domain's or
    the other's. The other's goes away asynchronously to this side's clock,
    with everything idle (pointers at 0, the processor in reset), so on
    whichever edge it lands, nothing moves. Not stream.ClockDomainCrossing's
    with_common_rst: its AsyncResetSynchronizers, lowered for simulation
    (litex/build/sim/common.py), all share one clock domain name, "resync", so
    with more than one in a design, all but one are clocked by the wrong
    clock."""
    def __init__(self, bus, wishbone_bus, cd="sys", io_regions={}, coprocessors=(), depth=8):
        cpu_side = M68030ToFIFO(bus, io_regions=io_regions, coprocessors=coprocessors)
        soc_side = FIFOToSoC(wishbone_bus)
        if cd == "sys":
            cd_cpu = cd_sys = "sys"
        else:
            cd_cpu, cd_sys = "m68k_bridge_cpu", "m68k_bridge_sys"
            self.cd_m68k_bridge_cpu = ClockDomain(cd_cpu)
            self.cd_m68k_bridge_sys = ClockDomain(cd_sys)
            either = ResetSignal(cd) | ResetSignal("sys")
            self.comb += [
                self.cd_m68k_bridge_cpu.clk.eq(ClockSignal(cd)),
                self.cd_m68k_bridge_cpu.rst.eq(either),
                self.cd_m68k_bridge_sys.clk.eq(ClockSignal("sys")),
                self.cd_m68k_bridge_sys.rst.eq(either),
            ]
        self.cpu_side = ClockDomainsRenamer(cd_cpu)(cpu_side)
        self.soc_side = ClockDomainsRenamer(cd_sys)(soc_side)
        self.request_cdc = stream.ClockDomainCrossing(request_layout(),
            cd_from=cd_cpu, cd_to=cd_sys, depth=depth)
        self.response_cdc = stream.ClockDomainCrossing(response_layout(),
            cd_from=cd_sys, cd_to=cd_cpu, depth=4)
        self.comb += [
            self.cpu_side.request.connect(self.request_cdc.sink),
            self.request_cdc.source.connect(self.soc_side.request),
            self.soc_side.response.connect(self.response_cdc.sink),
            self.response_cdc.source.connect(self.cpu_side.response),
        ]

    def add_native_port(self, port, origin, size):
        self.soc_side.add_native_port(port, origin, size)
