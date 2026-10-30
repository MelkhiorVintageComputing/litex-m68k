| SPDX-License-Identifier: BSD-2-Clause
| Copyright 2026 Romain Dolbeau

        .section .text.start, "ax"
        .globl  _start
_start:
        lea     stack_top, %sp
        jsr     bidump
1:      bra     1b

        .bss
        .balign 4
        .space  4096
stack_top:
