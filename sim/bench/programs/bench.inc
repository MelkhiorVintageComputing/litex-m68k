| SPDX-License-Identifier: BSD-2-Clause
| Copyright 2026 Romain Dolbeau

| The L1 bench's map (sim/bench/bench.py).

        .equ    DEV,            0xF0000000
        .equ    DEV_TTY,        DEV+0x00
        .equ    DEV_EXIT,       DEV+0x04
        .equ    DEV_RESULT,     DEV+0x08
        .equ    DEV_IPL,        DEV+0x0C
        .equ    DEV_COUNT,      DEV+0x10
        .equ    DEV_SCRATCH,    DEV+0x14

        .equ    ERR_SLAVE,      0xE0000000      | answers with a Wishbone error
        .equ    NOWHERE,        0x80000000      | nothing: the bridge times out

        | CACR (UM 6.3.1)
        .equ    CACR_EI,        0x0001
        .equ    CACR_CI,        0x0008
        .equ    CACR_IBE,       0x0010
        .equ    CACR_ED,        0x0100
        .equ    CACR_CD,        0x0800
        .equ    CACR_DBE,       0x1000
        .equ    CACR_WA,        0x2000
