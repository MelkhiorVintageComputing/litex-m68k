# SPDX-License-Identifier: BSD-2-Clause
# Copyright 2026 Romain Dolbeau

"""The SoC's side of the FIFO bridges, and the FIFOs, for any m68k bus.

A processor's side (M68030ToFIFO in m68030_fifo.py, M68040ToFIFO in
m68040_bus.py) runs on the processor's clock and FIFOToSoC on sys; between
them, two asynchronous FIFOs (M68kFIFOBridge), so the two clocks need not be
related at all: the processor's may be any frequency, and come from a pin, for
a real processor with its own oscillator.

- Requests, processor to SoC: read or write, word address, data and byte
  enables of a write, whether it is for a whole cache line, whether a write
  wants an answer, and a tag.
- Answers, SoC to processor: a cache line (128 bits, word k of the line, at
  byte offset 4k, in bits 32k+31..32k), an error bit and the tag of the request.

Requests are carried out in order, so a read always sees the writes before it.

Two request formats. Narrow (the MC68030's): a write is one long word, `dat`
and `sel` its own; only reads may be lines. Wide (the MC68040's, which also
writes lines, UM 7.4.4): `dat` and `sel` are a whole line, word k in bits
32k+31..32k and its byte enables in bits 4k+3..4k; a write of one long word
has its data and enables at its word of the line, and a line write (`line`
with `we`) writes all four.
"""

from migen import *

from litex.gen import *

from litex.soc.interconnect import stream

TAG_BITS = 3

def request_layout(wide=False):
    return [
        ("we",       1),  # 1: write.
        ("adr",     30),  # Word address.
        ("dat", 128 if wide else 32),  # Write data, big-endian (byte 0 in 31..24, as on D31-D0).
        ("sel",  16 if wide else  4),  # Write byte enables (sel[3]: byte 0).
        ("line",     1),  # The whole line (adr[0:2] is the word wanted first).
        ("answer",   1),  # Write: answer it (reads are always answered).
        ("tag", TAG_BITS),
    ]

def response_layout():
    return [
        ("dat",    128),  # The line: word k in bits 32k+31..32k. A single word in all four.
        ("err",      1),  # Bus error.
        ("tag", TAG_BITS),
    ]

# The SoC's side -----------------------------------------------------------------------------------

class FIFOToSoC(LiteXModule):
    """Carries out the requests from `request`, in order, one at a time, as a
    Wishbone master, or on a LiteDRAM native port for main RAM
    (add_native_port), and answers on `response` the reads and the writes that
    want it.

    Wishbone: a line is four accesses, a word one; a word read has its long
    word in all four words of the answer. An error, or no answer from the bus,
    is an error in the answer; a time-out is the bus's business
    (WishboneErrorTimeout in cpu/rd68031/core.py), as a Wishbone master cannot
    end an access by itself.

    Native port: as wide as a line. Every read reads the line; a write writes its
    long word in it, or, wide, the bytes its enables give."""
    def __init__(self, wishbone_bus, wide=False):
        self.wide     = wide
        self.request  = stream.Endpoint(request_layout(wide))
        self.response = stream.Endpoint(response_layout())
        self.wishbone = wishbone_bus
        assert wishbone_bus.data_width == 32 and wishbone_bus.addressing == "word"
        self.port     = None

    def add_native_port(self, port, origin, size):
        assert len(port.wdata.data) == 128, "the native port must be as wide as a line"
        assert origin % 16 == 0 and size % 16 == 0
        self.port, self.port_origin, self.port_size = port, origin, size

    def do_finalize(self):
        req, rsp, wb, port, wide = self.request, self.response, self.wishbone, self.port, self.wide

        # The request being carried out, taken from its FIFO at once: the
        # processor's side may then go on with the next (a posted write), and
        # without FIFOs (both sides in sys), it waits for its request to be
        # taken before it waits for the answer.
        r    = Record(request_layout(wide))
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
            ]
            if wide:
                self.comb += [
                    port.wdata.data.eq(r.dat),
                    port.wdata.we.eq(r.sel),
                ]
            else:
                self.comb += [
                    port.wdata.data.eq(Replicate(r.dat, 4)),
                    port.wdata.we.eq(r.sel << Cat(Constant(0, 2), r.adr[0:2])),
                ]

        self.comb += [
            wb.adr.eq(Mux(r.line, Cat(n, r.adr[2:]), r.adr)),
            wb.we.eq(r.we),
        ]
        if wide:
            # The word of the line this access is for.
            k = Signal(2)
            self.comb += [
                k.eq(Mux(r.line, n, r.adr[0:2])),
                wb.sel.eq(Mux(r.we, Array(r.sel[4*i:4*(i + 1)] for i in range(4))[k], 0b1111)),
                wb.dat_w.eq(Array(r.dat[32*i:32*(i + 1)] for i in range(4))[k]),
            ]
        else:
            self.comb += [
                wb.sel.eq(Mux(r.we, r.sel, 0b1111)),
                wb.dat_w.eq(r.dat),
            ]

        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            NextValue(err, 0),
            NextValue(n, 0),
            req.ready.eq(1),
            If(req.valid,
                [NextValue(getattr(r, name), getattr(req, name)) for name, _ in request_layout(wide)],
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

class M68kFIFOBridge(LiteXModule):
    """A processor's side `cpu_side` (with `request` and `response` endpoints) in
    clock domain `cd`, FIFOToSoC in sys, and the FIFOs between them (none when
    cd is sys). `wishbone_bus` is the SoC's side Wishbone master (in sys).

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
    def __init__(self, cpu_side, wishbone_bus, cd="sys", wide=False, depth=8):
        soc_side = FIFOToSoC(wishbone_bus, wide=wide)
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
        self.request_cdc = stream.ClockDomainCrossing(request_layout(wide),
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
