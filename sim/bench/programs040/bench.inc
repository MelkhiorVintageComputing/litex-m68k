| SPDX-License-Identifier: BSD-2-Clause
| Copyright 2026 Romain Dolbeau

| The L1 bench's map (sim/bench/bench.py), for RD68041 (an MC68040).

        .equ    DEV,            0xF0000000
        .equ    DEV_TTY,        DEV+0x00
        .equ    DEV_EXIT,       DEV+0x04
        .equ    DEV_RESULT,     DEV+0x08
        .equ    DEV_IPL,        DEV+0x0C
        .equ    DEV_COUNT,      DEV+0x10
        .equ    DEV_SCRATCH,    DEV+0x14

        .equ    ERR_SLAVE,      0xE0000000      | answers with a Wishbone error
        .equ    NOWHERE,        0x80000000      | nothing: the bridge times out

        | The RAM repeats every 256 KB up to $0FFF_FFFF. Below ALIAS, DTT1 makes
        | it copyback; from ALIAS, no TTR matches, which with translation off is
        | cachable, write-through (UM 3.1.2): the same memory, another cache mode.
        .equ    ALIAS,          0x01000000

        | CACR (UM 4.2)
        .equ    CACR_DE,        0x80000000
        .equ    CACR_IE,        0x00008000

        | Transparent translation registers (UM 3.1.3): base A31-A24, mask
        | A31-A24, E, S ignored, CM.
        .equ    TTR_E,          0x8000
        .equ    TTR_S_ANY,      0x4000
        .equ    TTR_CM_WT,      0x0000          | cachable, write-through
        .equ    TTR_CM_CB,      0x0020          | cachable, copyback
        .equ    TTR_CM_SER,     0x0040          | noncachable, serialized
        | DTT0: $8000_0000-$FFFF_FFFF, the I/O region, noncachable, serialized.
        .equ    DTT0_IO,        0x807F0000+TTR_E+TTR_S_ANY+TTR_CM_SER
        | DTT1: $0000_0000-$00FF_FFFF, the RAM below ALIAS, copyback.
        .equ    DTT1_RAM,       0x00000000+TTR_E+TTR_S_ANY+TTR_CM_CB
