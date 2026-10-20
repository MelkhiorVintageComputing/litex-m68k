| SPDX-License-Identifier: BSD-2-Clause
| Copyright 2026 Romain Dolbeau

| L2 smoke test runtime: the vectors, the stack, .bss, main, then a branch to
| itself. An exception nobody expects prints its format/vector word (main.c's
| unexpected()) and ends the simulation.

        .section .vectors, "a"
        .long   __stack_top             | initial interrupt stack pointer (UM 8.1.1)
        .long   _start                  | initial program counter
        .rept   24                      | vectors 2-25
        .long   _exception
        .endr
        .long   _irq                    | vector 26: level 2 autovector (core.py, IRQ_LEVEL)
        .rept   229                     | vectors 27-255
        .long   _exception
        .endr

        .text
        .globl  _start
_start:
        move.l  #__stack_top, %sp
        lea     __bss_start, %a0
        lea     __bss_end, %a1
        bra     2f
1:      clr.b   (%a0)+
2:      cmp.l   %a1, %a0
        bne     1b
        jsr     main
        bra     .

| The LiteX interrupt: C saves d2-d7/a2-a6 itself (m68k SysV ABI).
_irq:
        movem.l %d0-%d1/%a0-%a1, -(%sp)
        jsr     irq_handler
        movem.l (%sp)+, %d0-%d1/%a0-%a1
        rte

_exception:
        moveq   #0, %d0
        move.w  6(%sp), %d0             | format and vector offset (UM 8.2)
        move.l  %d0, -(%sp)
        jsr     unexpected
        bra     .
