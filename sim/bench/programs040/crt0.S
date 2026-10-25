| SPDX-License-Identifier: BSD-2-Clause
| Copyright 2026 Romain Dolbeau

| L1 bench runtime for RD68041: the vector table, the stack, .bss, the caches
| on (the RAM copyback, the I/O region noncachable and serialized), main, and
| the verdict. main's return value goes to RESULT, then EXIT 0; tb.sv compares
| RESULT with its +expect=. Any exception nobody installed a handler for ends
| the run with EXIT $BAD and the format/vector word in RESULT.

#include "bench.inc"

        .section .vectors, "a"
        .long   __stack_top             | initial interrupt stack pointer (UM 8.1.1)
        .long   _start                  | initial program counter
        .rept   254
        .long   _unexpected
        .endr

        .text
        .globl  _start
_start:
        andi    #0x00, %ccr
        move.l  #__stack_top, %sp

        move.l  #DTT0_IO, %d0
        movec   %d0, %dtt0
        move.l  #DTT1_RAM, %d0
        movec   %d0, %dtt1
        cinva   %bc                     | nothing valid out of reset (UM 4.4)
        move.l  #(CACR_DE+CACR_IE), %d0
        movec   %d0, %cacr

        lea     __bss_start, %a0
        lea     __bss_end, %a1
        bra     2f
1:      clr.b   (%a0)+
2:      cmp.l   %a1, %a0
        bne     1b

        jsr     main

        move.l  %d0, DEV_RESULT
        clr.l   DEV_EXIT
        bra     .

        .globl  _unexpected
_unexpected:
        moveq   #0, %d0
        move.w  6(%sp), %d0             | format and vector offset (UM 8.2)
        move.l  %d0, DEV_RESULT
        move.l  #0xBAD, DEV_EXIT
        bra     .

| void puts(const char *s)
        .globl  puts
puts:
        move.l  4(%sp), %a0
        moveq   #0, %d0
        bra     2f
1:      move.l  %d0, DEV_TTY
2:      move.b  (%a0)+, %d0
        bne     1b
        rts
