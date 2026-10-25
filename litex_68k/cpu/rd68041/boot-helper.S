| SPDX-License-Identifier: BSD-2-Clause
| Copyright 2026 Romain Dolbeau

| void boot_helper(unsigned long r1, unsigned long r2, unsigned long r3,
|                  unsigned long addr)
|
| Called by the BIOS's boot() with interrupts off and the caches flushed.
| Jumps to addr, the image's first byte, which is its crt0's BRA to _start
| (crt0.S). r1-r3 are passed in d1-d3 for a program that wants them.

        .section .text, "ax", @progbits
        .globl  boot_helper
boot_helper:
        move.l  4(%sp), %d1
        move.l  8(%sp), %d2
        move.l  12(%sp), %d3
        movea.l 16(%sp), %a0
        jmp     (%a0)
