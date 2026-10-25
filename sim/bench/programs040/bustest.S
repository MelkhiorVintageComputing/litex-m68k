| SPDX-License-Identifier: BSD-2-Clause
| Copyright 2026 Romain Dolbeau

| L1 bus test for RD68041: what M68040ToFIFOToSoC has to get right, checked by
| the program itself. main returns $0040600D when everything passed, otherwise
| the number of the first check that failed (tb.sv: +expect=0040600d): not 0,
| which a run gone astray may reach crt0's end with.
|
|  1-29  byte lanes: every size at every offset, aligned and misaligned, written
|        and read back with the data cache off, so that every access is a bus
|        transfer (the processor splits a misaligned operand, UM 7.3, Table 7-1)
| 30-32  byte enables on an I/O register
| 40-43  TAS and CAS: locked transfers (UM 7.4.5), in RAM and on an I/O register
| 50-55  access errors: a Wishbone error, a timeout (read and write), and a
|        breakpoint acknowledge, which the bridge ends with TEA (UM 7.5.2)
| 60-64  interrupts: autovectored level 3, masked and unmasked (UM 7.5.1.2)
| 70-73  TCI: a device register read twice through a cachable mode, then
|        through DTT0's noncachable one, is read from the device each time
| 80-83  a line read in wrap order: a miss at a line's third long word (UM 7.4.2)
| 84-87  a line write: a copyback line pushed (CPUSHL, UM 4.2, 7.4.4)
| 88-91  MOVE16: a line read and a line write (UM 7.4.2, 7.4.4, TT = 1)

#include "bench.inc"

        | Check that <ea> (of size .s) equals #v, else fail with number n.
        .macro  CHECK s, v, ea, n
        moveq   #\n, %d7
        cmp.\s  #\v, \ea
        bne     fail
        .endm

        .text
        .globl  main
main:
        movem.l %d2-%d7/%a2-%a6, -(%sp)
        move.l  %sp, saved_sp

        pea     msg_start
        jsr     puts
        addq.l  #4, %sp

| ---- byte lanes, data cache off ------------------------------------------
        | Copyback lines out first: once the cache is off, nothing reads them.
        cpusha  %dc
        move.l  #CACR_IE, %d0
        movec   %d0, %cacr

        lea     buf, %a2
        clr.l   (%a2)
        clr.l   4(%a2)
        clr.l   8(%a2)
        clr.l   12(%a2)

        move.b  #0xA1, 0(%a2)           | one byte in each lane
        move.b  #0xB2, 1(%a2)
        move.b  #0xC3, 2(%a2)
        move.b  #0xD4, 3(%a2)
        CHECK   l, 0xA1B2C3D4, (%a2), 1

        move.w  #0x1122, 1(%a2)         | a word in the middle lanes: two bytes
        CHECK   l, 0xA11122D4, (%a2), 2

        move.w  #0x3344, 3(%a2)         | a word across a long-word boundary
        CHECK   l, 0xA1112233, (%a2), 3
        CHECK   l, 0x44000000, 4(%a2), 4

        move.l  #0x55667788, 5(%a2)     | a byte, a word, a byte
        CHECK   l, 0x44556677, 4(%a2), 5
        CHECK   l, 0x88000000, 8(%a2), 6

        move.l  #0x99AABBCC, 10(%a2)    | a word, then a word
        CHECK   l, 0x880099AA, 8(%a2), 7
        CHECK   l, 0xBBCC0000, 12(%a2), 8

        move.l  #0xDDEEFF01, 7(%a2)     | a byte, then a word and a byte
        CHECK   l, 0x445566DD, 4(%a2), 9
        CHECK   l, 0xEEFF01AA, 8(%a2), 10

        move.w  #0x1234, 2(%a2)         | an aligned word in the low lanes
        CHECK   l, 0xA1111234, (%a2), 11

        move.l  #0x0BADCAFE, 12(%a2)    | an aligned long word
        CHECK   l, 0x0BADCAFE, 12(%a2), 12

        | Reads at every size and offset of the 16 bytes now there:
        | A1 11 12 34 | 44 55 66 DD | EE FF 01 AA | 0B AD CA FE
        CHECK   b, 0xA1, 0(%a2), 13
        CHECK   b, 0x11, 1(%a2), 14
        CHECK   b, 0x12, 2(%a2), 15
        CHECK   b, 0x34, 3(%a2), 16
        CHECK   w, 0xA111, 0(%a2), 17
        CHECK   w, 0x1112, 1(%a2), 18
        CHECK   w, 0x1234, 2(%a2), 19
        CHECK   w, 0x3444, 3(%a2), 20
        CHECK   l, 0x11123444, 1(%a2), 21
        CHECK   l, 0x12344455, 2(%a2), 22
        CHECK   l, 0x34445566, 3(%a2), 23
        CHECK   l, 0x66DDEEFF, 6(%a2), 24
        CHECK   l, 0xDDEEFF01, 7(%a2), 25
        CHECK   w, 0xDDEE, 7(%a2), 26
        CHECK   l, 0xAA0BADCA, 11(%a2), 27
        CHECK   w, 0xFE00, 15(%a2), 28  | runs into the next 16 bytes...
        CHECK   b, 0x00, 16(%a2), 29    | ... which are buf2, zero

| ---- byte enables on an I/O register --------------------------------------
        move.l  #0x01020304, DEV_SCRATCH
        CHECK   l, 0x01020304, DEV_SCRATCH, 30
        move.b  #0x5A, DEV_SCRATCH+2
        CHECK   l, 0x01025A04, DEV_SCRATCH, 31
        move.w  #0xC0DE, DEV_SCRATCH+2
        CHECK   l, 0x0102C0DE, DEV_SCRATCH, 32

| ---- locked transfers -------------------------------------------------------
        clr.l   (%a2)
        moveq   #40, %d7
        tas     (%a2)                   | was clear: Z set, and now $80
        bne     fail
        CHECK   l, 0x80000000, (%a2), 40
        move.l  #0x80000000, %d0
        move.l  #0x12345678, %d1
        moveq   #41, %d7
        cas.l   %d0, %d1, (%a2)         | equal: stores d1
        bne     fail
        CHECK   l, 0x12345678, (%a2), 41
        clr.l   DEV_SCRATCH
        moveq   #42, %d7
        tas     DEV_SCRATCH+1           | the same on a device, another lane
        bne     fail
        CHECK   l, 0x00800000, DEV_SCRATCH, 42
        move.l  #0x00800000, %d0
        move.l  #0xFEEDF00D, %d1
        moveq   #43, %d7
        cas.l   %d0, %d1, DEV_SCRATCH
        bne     fail
        CHECK   l, 0xFEEDF00D, DEV_SCRATCH, 43

| ---- access errors -----------------------------------------------------------
        move.l  #berr_handler, 2*4      | vector 2, access error
        move.l  #illegal_handler, 4*4   | vector 4, illegal instruction

        clr.l   berr_count
        lea     1f, %a3
        tst.l   ERR_SLAVE               | the Wishbone error slave
1:      CHECK   l, 1, berr_count, 50

        lea     1f, %a3
        tst.l   NOWHERE                 | nothing: the bus's timeout
1:      CHECK   l, 2, berr_count, 51

        lea     1f, %a3
        move.l  #0x12345678, NOWHERE    | the same, writing (I/O: not posted)
        nop                             | the write's error is taken here at the latest
1:      CHECK   l, 3, berr_count, 52

        lea     1f, %a3
        move.b  ERR_SLAVE+3, %d0        | a byte, in another lane
1:      CHECK   l, 4, berr_count, 53

        clr.l   illegal_count
        bkpt    #3                      | a breakpoint acknowledge: TEA
        CHECK   l, 1, illegal_count, 54
        CHECK   l, 4, berr_count, 55    | ... not an access error

| ---- interrupts --------------------------------------------------------------
        move.l  #irq3_handler, (24+3)*4 | level 3 autovector (UM Table 8-1)
        clr.l   irq_count
        move.w  #0x2700, %sr            | mask 7
        move.l  #3, DEV_IPL
        bsr     delay
        CHECK   l, 0, irq_count, 60     | masked
        move.w  #0x2300, %sr            | mask 3: level 3 still masked
        bsr     delay
        CHECK   l, 0, irq_count, 61
        move.w  #0x2200, %sr            | mask 2: taken
        bsr     delay
        CHECK   l, 1, irq_count, 62     | once: the handler lowers the level
        CHECK   l, 0, DEV_IPL, 63
        CHECK   l, 3, irq_level, 64
        move.w  #0x2700, %sr

| ---- TCI: device registers are not cached -----------------------------------
        cinva   %dc                     | lines read with the cache off may be stale
        move.l  #(CACR_DE+CACR_IE), %d0
        movec   %d0, %cacr
        moveq   #0, %d0
        movec   %d0, %dtt0              | the I/O region cachable, write-through
        move.l  DEV_COUNT, %d0          | a line read, which TCI keeps out
        move.l  DEV_COUNT, %d1
        move.l  #DTT0_IO, %d2
        movec   %d2, %dtt0
        moveq   #70, %d7
        cmp.l   %d0, %d1
        beq     fail
        addq.l  #1, %d0
        moveq   #71, %d7
        cmp.l   %d0, %d1
        bne     fail
        move.l  DEV_COUNT, %d0          | noncachable, serialized: long words
        move.l  DEV_COUNT, %d1
        moveq   #72, %d7
        cmp.l   %d0, %d1
        beq     fail
        addq.l  #1, %d0
        moveq   #73, %d7
        cmp.l   %d0, %d1
        bne     fail

| ---- line reads and writes --------------------------------------------------
        | a4: line, its write-through alias; the cache is on, the RAM below
        | ALIAS copyback.
        lea     line1, %a2
        lea     ALIAS(%a2), %a4
        move.l  #0x10101010, (%a4)      | write-through, not allocated: on the bus
        move.l  #0x20202020, 4(%a4)
        move.l  #0x30303030, 8(%a4)
        move.l  #0x40404040, 12(%a4)
        cinva   %dc
        move.l  8(%a2), %d0             | a miss: the line from its third long word
        CHECK   l, 0x10101010, (%a2), 80
        CHECK   l, 0x20202020, 4(%a2), 81
        CHECK   l, 0x30303030, 8(%a2), 82
        CHECK   l, 0x40404040, 12(%a2), 83

        lea     line2, %a2
        lea     ALIAS(%a2), %a4
        move.l  #0x55AA0001, (%a2)      | copyback: in the cache only
        move.l  #0x55AA0002, 4(%a2)
        move.l  #0x55AA0003, 8(%a2)
        move.l  #0x55AA0004, 12(%a2)
        cpushl  %dc, (%a2)              | the line written out, and invalidated
        CHECK   l, 0x55AA0001, (%a4), 84 | read back by the alias: from the RAM
        CHECK   l, 0x55AA0002, 4(%a4), 85
        CHECK   l, 0x55AA0003, 8(%a4), 86
        CHECK   l, 0x55AA0004, 12(%a4), 87

        lea     line3, %a2
        lea     ALIAS(%a2), %a4
        lea     line2+ALIAS, %a0
        move16  (%a0)+, (%a4)+          | line2 to line3, by the bus
        cinva   %dc
        CHECK   l, 0x55AA0001, (%a2), 88
        CHECK   l, 0x55AA0002, 4(%a2), 89
        CHECK   l, 0x55AA0003, 8(%a2), 90
        CHECK   l, 0x55AA0004, 12(%a2), 91

        pea     msg_pass
        jsr     puts
        addq.l  #4, %sp
        move.l  #0x0040600D, %d0
        bra     out

fail:   pea     msg_fail
        jsr     puts
        addq.l  #4, %sp
        move.l  %d7, %d0
out:    movem.l (%sp)+, %d2-%d7/%a2-%a6
        rts

delay:  move.w  #63, %d0
1:      dbra    %d0, 1b
        rts

| An access error: count it and carry on at (a3). The fault frame (format $7)
| is abandoned, not returned from.
berr_handler:
        addq.l  #1, berr_count
        movea.l saved_sp, %sp           | main's, after its movem
        jmp     (%a3)

| BKPT: count it and step over it (format $0: SR, then PC, at 2(sp)).
illegal_handler:
        addq.l  #1, illegal_count
        addq.l  #2, 2(%sp)
        rte

irq3_handler:
        move.l  DEV_IPL, irq_level
        clr.l   DEV_IPL
        addq.l  #1, irq_count
        rte

        .section .rodata
msg_start:  .asciz "bustest: "
msg_pass:   .asciz "all checks passed\n"
msg_fail:   .asciz "FAILED\n"

        .bss
        .balign 16
buf:    .space  16
buf2:   .space  16
line1:  .space  16
line2:  .space  16
line3:  .space  16
saved_sp:       .space 4
berr_count:     .space 4
illegal_count:  .space 4
irq_count:      .space 4
irq_level:      .space 4
