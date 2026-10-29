| SPDX-License-Identifier: BSD-2-Clause
| Copyright 2026 Romain Dolbeau

| smptest (L20): CPU 1's entry, and the exception handlers both processors use.


        .text

| CPU 1, sent here by crt0.S (BOOT_PC) with its stack (BOOT_SP), supervisor,
| interrupts masked, translation and caches off. It takes what CPU 0 prepared
| in cpu1_setup (main.c): the vector table, the TTRs, the tables, CACR. With
| translation off, this code and cpu1_setup are at their physical addresses,
| which translation maps one to one.
        .globl  cpu1_entry
cpu1_entry:
        lea     cpu1_setup, %a0
        move.l  (%a0)+, %d0             | VBR
        movec   %d0, %vbr
        move.l  (%a0)+, %d0             | DTT0
        movec   %d0, %dtt0
        move.l  (%a0)+, %d0             | DTT1
        movec   %d0, %dtt1
        moveq   #0, %d0
        movec   %d0, %itt0
        movec   %d0, %itt1
        move.l  (%a0)+, %d0             | the root table
        movec   %d0, %urp
        movec   %d0, %srp
        pflusha
        move.l  (%a0)+, %d0             | TC
        movec   %d0, %tc
        cinva   %bc
        move.l  (%a0)+, %d0             | CACR
        movec   %d0, %cacr
        jsr     cpu1_main
        bra     .

| Interrupts and the rest, for both processors. C handlers, which save
| d2-d7/a2-a6 themselves (the SysV ABI).
        .globl  ipi_handler
ipi_handler:                            | vector 29: the board's interprocessor interrupt
        movem.l %d0-%d1/%a0-%a1, -(%sp)
        jsr     ipi_c
        movem.l (%sp)+, %d0-%d1/%a0-%a1
        rte

        .globl  irq_handler
irq_handler:                            | vector 26: LiteX's interrupt lines
        movem.l %d0-%d1/%a0-%a1, -(%sp)
        jsr     irq_c
        movem.l (%sp)+, %d0-%d1/%a0-%a1
        rte

        .globl  unexpected_handler
unexpected_handler:                     | anything else: record it and stop
        ori.w   #0x0700, %sr
        moveq   #0, %d0
        move.w  6(%sp), %d0             | format/vector
        move.l  %d0, -(%sp)
        move.l  6(%sp), -(%sp)          | PC (2(sp) before the push)
        jsr     unexpected_c
1:      bra     1b

| A routine CPU 0 rewrites while CPU 1 runs it (the instruction cache, kept
| coherent by snooping): copied to the shared code page, then called there.
        .globl  routine_one, routine_one_end
routine_one:
        moveq   #1, %d0
        rts
routine_one_end:
