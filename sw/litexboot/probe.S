| SPDX-License-Identifier: BSD-2-Clause
| Copyright 2026 Romain Dolbeau

| litexboot's assembly: whether there is an FPU, and the jump to the kernel.

| fpu_probe(): 1 if FNOP runs, 0 if it takes the line F exception (MC68030: no
| coprocessor answers, UM 10.1.1; MC68LC040: no FPU, MC68040 UM A.5). main.c
| points vector 11 at fline_catch for the call. As words: the assembler takes
| no FPU instruction in a soft-float build.
        .text
        .globl  fpu_probe
fpu_probe:
        moveq   #1, %d0
        .word   0xf280, 0x0000          | fnop
        rts

| Line F: a format $0 frame, its PC the FNOP's (UM 8.1): skip it. Or, on the
| MC68LC040, a format $4 one, its PC already the next instruction's (MC68040
| UM A.5.1). Say none.
        .globl  fline_catch
fline_catch:
        moveq   #0, %d0
        cmpi.b  #0x40, 6(%sp)           | the format, in the high nibble
        bhs     1f
        addq.l  #4, 2(%sp)
1:      rte

| jump_kernel(entry): IPL 7, both caches cleared and off, MMU off as it has
| always been, and on to the kernel; head.S sets its own stack and VBR.
| MC68030: CACR CI and CD with EI and ED clear (UM 6.3.1). MC68040: CPUSHA
| (nothing is dirty in crt0's write-through mode, but a kernel's tables must
| be in memory), CACR 0, and the transparent translation registers crt0 set
| cleared, as out of reset (MC68040 UM 4.2, 3.1.3).
        .globl  jump_kernel
jump_kernel:
        move.l  4(%sp), %a0
        move.w  #0x2700, %sr
#ifdef __rd68041__
        cpusha  %bc
        moveq   #0, %d0
        movec   %d0, %cacr
        cinva   %bc
        movec   %d0, %dtt0
        movec   %d0, %dtt1
        movec   %d0, %itt0
        movec   %d0, %itt1
#else
        move.l  #0x0808, %d0
        movec   %d0, %cacr
#endif
        jmp     (%a0)
