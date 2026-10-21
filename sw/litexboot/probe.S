| SPDX-License-Identifier: BSD-2-Clause
| Copyright 2026 Romain Dolbeau

| litexboot's assembly: whether there is an FPU, and the jump to the kernel.

| fpu_probe(): 1 if FNOP runs, 0 if it takes the line F exception (no
| coprocessor answers, UM 10.1.1). main.c points vector 11 at fline_catch for
| the call. As words: the assembler takes no FPU instruction in a soft-float
| build.
        .text
        .globl  fpu_probe
fpu_probe:
        moveq   #1, %d0
        .word   0xf280, 0x0000          | fnop
        rts

| Line F: a format $0 frame, its PC the FNOP's (UM 8.1). Skip it, say none.
        .globl  fline_catch
fline_catch:
        moveq   #0, %d0
        addq.l  #4, 2(%sp)
        rte

| jump_kernel(entry): IPL 7, both caches cleared and off (CACR CI and CD
| with EI and ED clear, UM 6.3.1), MMU off as it has always been, and on to
| the kernel; head.S sets its own stack and VBR.
        .globl  jump_kernel
jump_kernel:
        move.l  4(%sp), %a0
        move.w  #0x2700, %sr
        move.l  #0x0808, %d0
        movec   %d0, %cacr
        jmp     (%a0)
