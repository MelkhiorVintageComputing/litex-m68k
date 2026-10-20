| SPDX-License-Identifier: BSD-2-Clause
| Copyright 2026 Romain Dolbeau

| L1 bus test: what M68030ToWishbone has to get right, checked by the program
| itself. main returns 0 when everything passed, otherwise the number of the
| first check that failed (tb.sv: +expect=00000000).
|
|  1-29  byte lanes: every size at every offset, aligned, misaligned and across
|        long words, written and read back with the data cache off, so that
|        every access is a bus cycle (UM 7.2, Tables 7-4 and 7-5)
| 30-32  byte enables on an I/O register
| 40-41  TAS and CAS: read-modify-write cycles (UM 7.6)
| 50-55  bus errors: a Wishbone error, a timeout (read and write), and a
|        breakpoint acknowledge cycle, which nothing answers (UM 7.4.2)
| 60-64  interrupts: autovectored level 3, masked and unmasked (UM 7.4.1.2)
| 70-71  CIIN: a device register read twice with the data cache on is read twice

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
        move.l  #(CACR_CD+CACR_IBE+CACR_EI), %d0
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

        move.w  #0x1122, 1(%a2)         | a word in the middle lanes
        CHECK   l, 0xA11122D4, (%a2), 2

        move.w  #0x3344, 3(%a2)         | a word across a long-word boundary
        CHECK   l, 0xA1112233, (%a2), 3
        CHECK   l, 0x44000000, 4(%a2), 4

        move.l  #0x55667788, 5(%a2)     | 3 bytes, then 1
        CHECK   l, 0x44556677, 4(%a2), 5
        CHECK   l, 0x88000000, 8(%a2), 6

        move.l  #0x99AABBCC, 10(%a2)    | 2 bytes, then 2
        CHECK   l, 0x880099AA, 8(%a2), 7
        CHECK   l, 0xBBCC0000, 12(%a2), 8

        move.l  #0xDDEEFF01, 7(%a2)     | 1 byte, then 3
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
        move.w  #0xC0DE, DEV_SCRATCH+1
        CHECK   l, 0x01C0DE04, DEV_SCRATCH, 32

| ---- read-modify-write -----------------------------------------------------
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

| ---- bus errors ------------------------------------------------------------
        move.l  #berr_handler, 2*4      | vector 2, bus error
        move.l  #illegal_handler, 4*4   | vector 4, illegal instruction

        clr.l   berr_count
        lea     1f, %a3
        tst.l   ERR_SLAVE               | the Wishbone error slave
1:      CHECK   l, 1, berr_count, 50

        lea     1f, %a3
        tst.l   NOWHERE                 | nothing: the bridge's timeout
1:      CHECK   l, 2, berr_count, 51

        lea     1f, %a3
        move.l  #0x12345678, NOWHERE    | the same, writing
1:      CHECK   l, 3, berr_count, 52

        lea     1f, %a3
        move.b  ERR_SLAVE+3, %d0        | a byte, in another lane
1:      CHECK   l, 4, berr_count, 53

        clr.l   illegal_count
        bkpt    #3                      | a breakpoint acknowledge in CPU space
        CHECK   l, 1, illegal_count, 54
        CHECK   l, 4, berr_count, 55    | ... answered with BERR, not counted here

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

| ---- CIIN: device registers are not cached ---------------------------------
        move.l  #(CACR_WA+CACR_ED+CACR_IBE+CACR_EI), %d0
        movec   %d0, %cacr
        move.l  DEV_COUNT, %d0
        move.l  DEV_COUNT, %d1
        moveq   #70, %d7
        cmp.l   %d0, %d1
        beq     fail
        addq.l  #1, %d0
        moveq   #71, %d7
        cmp.l   %d0, %d1
        bne     fail

        pea     msg_pass
        jsr     puts
        addq.l  #4, %sp
        moveq   #0, %d0
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

| A bus error: count it and carry on at (a3). The fault frame is abandoned, not
| returned from: an RTE would rerun the faulted access (UM 8.2.1).
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
saved_sp:       .space 4
berr_count:     .space 4
illegal_count:  .space 4
irq_count:      .space 4
irq_level:      .space 4
