| SPDX-License-Identifier: BSD-2-Clause
| Copyright 2026 Romain Dolbeau

| RD68041 startup for LiteX: the BIOS in the ROM, and the programs it boots.
|
| The linker scripts put crt0's .text first, so the vector table (UM 8.1,
| Table 8-1) is the first thing of every image:
|
| - At 0, in the ROM, the processor reads the reset SSP and PC from it
|   (UM 8.1).
| - A program loaded into RAM is entered at its first byte (the BIOS boots the
|   load address). So the first long word, which only the reset reads, and
|   only as the initial SSP, holds a BRA.W to _start. At reset that makes a
|   meaningless SSP, which nothing uses: _start loads the stack pointer before
|   anything touches the stack.
|
| _start points VBR at this image's own table, so the BIOS and what it boots
| each take their exceptions in their own handlers.
|
| Interrupts: every LiteX interrupt line raises IPL 2, autovectored (core.py,
| IRQ_LEVEL; irq.h), so vector 26 goes to LiteX's isr(). Every other vector
| prints what it can and stops.

#include "system.h"           /* this directory's: LiteX's own is C only */

        .section .text, "ax", @progbits
        .globl  _vectors
_vectors:
        bra.w   _start                  | 0: reset SSP, and the entry from RAM
        .long   _start                  | 1: reset PC
        .rept   24                      | 2-25: bus error to spurious interrupt
        .long   _exception
        .endr
        .long   _irq                    | 26: level 2 autovector
        .rept   229                     | 27-255
        .long   _exception
        .endr

        .globl  _start
_start:
        move.w  #0x2700, %sr            | supervisor, interrupts masked
#ifdef __rd68041_smp__
        | Two processors (core.py "+smp"): both reset into this ROM. CPU 1
        | (WHOAMI non-zero) touches nothing of the BIOS's: it waits for
        | BOOT_PC, takes BOOT_SP, and goes there (smp.h, smp_start_cpu1). The
        | board's registers are in the I/O region; out of reset the caches are
        | off, and the board answers with TCI anyway.
        tst.l   SMP_WHOAMI
        beq.s   1f
0:      move.l  SMP_BOOT_PC, %d0
        beq.s   0b
        movea.l SMP_BOOT_SP, %sp
        movea.l %d0, %a0
        jmp     (%a0)
1:
#endif
        move.l  #_fstack, %sp
        lea     _vectors, %a0
        movec   %a0, %vbr

        | The I/O region noncachable and serialized (DTT0, UM 3.1.3), so device
        | registers are read and written in program order and never cached;
        | the bridge's TCI keeps them out of the caches too. Everything else,
        | with translation off and no other TTR, is cachable write-through
        | (UM 3.1.2): nothing is ever dirty, which keeps DMA simple. Then both
        | caches invalidated and on. The caches' contents are undefined out of
        | reset (UM 4.2), and an image the BIOS boots was flushed by it
        | (boot.c), so CINVA loses nothing.
        move.l  #DTT_IO, %d0
        movec   %d0, %dtt0
        moveq   #0, %d0
        movec   %d0, %dtt1
        movec   %d0, %itt0
        movec   %d0, %itt1
        cinva   %bc
        move.l  #(CACR_DE+CACR_IE), %d0
        movec   %d0, %cacr

        | .data, from where the image has it to where the program uses it.
        lea     _fdata_rom, %a0
        lea     _fdata, %a1
        lea     _edata, %a2
        cmpa.l  %a0, %a1
        beq     2f
        bra     1f
0:      move.l  (%a0)+, (%a1)+
1:      cmpa.l  %a2, %a1
        blo     0b
2:
        | .bss
        lea     _fbss, %a0
        lea     _ebss, %a1
        bra     1f
0:      clr.l   (%a0)+
1:      cmpa.l  %a1, %a0
        blo     0b

        jsr     litex_startup_init
        jsr     main
        bra     .

| The LiteX interrupt. isr() is C: it saves d2-d7/a2-a6 itself (m68k SysV ABI).
        .section .text.isr, "ax", @progbits
_irq:
        movem.l %d0-%d1/%a0-%a1, -(%sp)
        jsr     isr
        movem.l (%sp)+, %d0-%d1/%a0-%a1
        rte

| Anything else: say what it was and stop. Every frame starts with SR, PC and
| the format/vector word (UM 8.2). The interrupt mask goes to 7 first, so the
| UART driver polls instead of waiting for an interrupt that cannot come.
_exception:
        ori.w   #0x0700, %sr
        moveq   #0, %d0
        move.w  (%sp), %d0              | SR
        move.l  %d0, -(%sp)
        move.l  6(%sp), -(%sp)          | PC (2(sp) before the push)
        moveq   #0, %d0
        move.w  14(%sp), %d0            | format/vector (6(sp) before the pushes)
        move.l  %d0, -(%sp)
        pea     _exception_msg
        jsr     printf
        | With interrupts masked, libbase's uart_write leaves what the UART's
        | FIFO cannot take in its ring until the ring fills: send it all
        | before stopping. uart_sync exists when the SoC has a UART.
        lea     uart_sync, %a0
        cmpa.w  #0, %a0
        beq     1f
        jsr     (%a0)
1:      bra     .

        .weak   uart_sync

        .section .rodata
_exception_msg:
        .asciz  "\n*** RD68041 exception: format/vector %04lx, PC %08lx, SR %04lx\n"
