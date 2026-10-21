| SPDX-License-Identifier: BSD-2-Clause
| Copyright 2026 Romain Dolbeau

| fpswitch's interrupt: the LiteX interrupt, and on timer0's a switch to the
| other task, saving and restoring the FPU the way Linux's resume does
| (arch/m68k/kernel/entry.S, the 030 path): FSAVE; if the frame is not null
| (its first byte), FMOVEM of the data and control registers after it; then,
| for the incoming task, FMOVEM back if not null and FRESTORE last.
|
| struct task (main.c): +0 the stack pointer, +4 the FSAVE frame (256 bytes),
| +260 fp0-fp7 (96), +356 fpcr/fpsr/fpiar (12).

        .section .text
        .globl  fpsw_irq
fpsw_irq:
        movem.l %d0-%d7/%a0-%a6, -(%sp)
        jsr     fpsw_tick               | 1: switch
        tst.l   %d0
        beq     9f

        move.l  fpsw_cur, %a0
        move.l  %sp, (%a0)
        fsave   4(%a0)
        moveq   #0, %d0
        move.b  5(%a0), %d0             | the frame's size byte
        tst.b   4(%a0)
        bne     1f
        move.w  #256, %d0               | null: counted apart
1:      lea     fpsw_frames, %a1
        addq.l  #1, (%a1,%d0.l*4)
        tst.b   4(%a0)
        beq     2f
        fmovem.x %fp0-%fp7, 260(%a0)
        fmovem.l %fpcr/%fpsr/%fpiar, 356(%a0)
2:
        move.l  fpsw_other, %a1
        move.l  %a0, fpsw_other
        move.l  %a1, fpsw_cur
        tst.b   4(%a1)
        beq     3f
        fmovem.x 260(%a1), %fp0-%fp7
        fmovem.l 356(%a1), %fpcr/%fpsr/%fpiar
3:      frestore 4(%a1)
        move.l  (%a1), %sp
9:
        movem.l (%sp)+, %d0-%d7/%a0-%a6
        rte

| A task's first switch in: its stack as fpsw_irq leaves one, a four-word
| frame (format 0, vector 26) that RTEs to its entry, under fifteen zeros.
| void fpsw_prime(struct task *t, void *stack_top, void (*entry)(void))
        .globl  fpsw_prime
fpsw_prime:
        move.l  4(%sp), %a0
        move.l  8(%sp), %a1
        move.w  #0x0068, -(%a1)         | format 0, vector 26 (offset 0x68)
        move.l  12(%sp), -(%a1)         | PC
        move.w  #0x2000, -(%a1)         | SR: supervisor, IPL 0
        moveq   #14, %d0
1:      clr.l   -(%a1)
        dbra    %d0, 1b
        move.l  %a1, (%a0)
        rts
