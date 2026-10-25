| SPDX-License-Identifier: BSD-2-Clause
| Copyright 2026 Romain Dolbeau

| cputest040: running code that is expected to take an exception, and the code.
|
| trap_run(fn) calls fn with every exception vector pointing at trap_catch
| (main.c builds that table). If fn returns, trap_run returns 0. If fn takes an
| exception, trap_catch copies the start of the frame to trap_frame, drops the
| frame, restores the stack pointer and SR trap_run saved, and trap_run returns
| the frame's format/vector word. Nothing returns from these exceptions with
| RTE: the frame is looked at, not resumed, so one handler fits every kind of
| frame (UM 8.4), access error frames included, whose pending write-backs are
| dropped with them.

        .text
        .globl  trap_run
trap_run:
        move.l  4(%sp), %a0
        movem.l %d2-%d7/%a2-%a6, -(%sp)
        move.w  %sr, -(%sp)
        move.l  %sp, trap_sp
        jsr     (%a0)
        moveq   #0, %d0
trap_return:
        move.w  (%sp)+, %sr
        movem.l (%sp)+, %d2-%d7/%a2-%a6
        rts

        .globl  trap_catch
trap_catch:
        | The first $18 bytes of the frame: SR, PC, format/vector, then for
        | format $2 the address (+8), for an access error (format $7, UM 8.4.6)
        | the SSW (+$C) and the fault address (+$14). Shorter frames copy what
        | lies beyond them, which nobody reads.
        lea     trap_frame, %a0
        move.l  (%sp), (%a0)+
        move.l  4(%sp), (%a0)+
        move.l  8(%sp), (%a0)+
        move.l  12(%sp), (%a0)+
        move.l  16(%sp), (%a0)+
        move.l  20(%sp), (%a0)+
        moveq   #0, %d0
        move.w  6(%sp), %d0
        move.l  trap_sp, %sp
        bra     trap_return

        .bss
        .globl  trap_frame
        .even
trap_sp:        .space  4
trap_frame:     .space  24

| The tests. Each is a function for trap_run, with labels for the addresses
| the frame should hold: <name>_insn, the instruction that faults, and
| <name>_next, the one after it.

        .text
        .macro  test name
        .globl  \name, \name\()_insn, \name\()_next
\name:
        .endm

        test    t_illegal
t_illegal_insn:
        illegal
t_illegal_next:
        rts

        test    t_zerodiv
        moveq   #0, %d1
        moveq   #1, %d0
t_zerodiv_insn:
        divu.w  %d1, %d0
t_zerodiv_next:
        rts

        test    t_chk                   | 5 is out of [0, 3]
        moveq   #5, %d0
        moveq   #3, %d1
t_chk_insn:
        chk.w   %d1, %d0
t_chk_next:
        rts

        test    t_trapv
        move.w  #2, %ccr                | V
t_trapv_insn:
        trapv
t_trapv_next:
        rts

        test    t_trap5
t_trap5_insn:
        trap    #5
t_trap5_next:
        rts

        test    t_linea
t_linea_insn:
        .word   0xa000
t_linea_next:
        rts

| An F-line word the MC68040 does not know: coprocessor ID 7, neither the FPU
| (1), nor MOVE16 ($F6xx: CpID 3 is MOVE16 on the 040), nor the cache and MMU
| instructions ($F4xx, $F5xx). An F-line exception, format $0 (UM 9.6.1).
        test    t_linef
t_linef_insn:
        .word   0xfe00, 0x0000
t_linef_next:
        rts

| Down to user mode, then a privileged instruction.
        test    t_priv
        lea     user_stack_top, %a0
        move.l  %a0, %usp
        andi.w  #0xdfff, %sr
t_priv_insn:
        move.w  #0x2700, %sr
t_priv_next:
        rts

| T1 set by the ORI: the trace exception comes after the next instruction
| (UM 8.2.6), a format $2 frame whose PC is the instruction after the traced
| one and whose address is the traced one.
        test    t_trace
        ori.w   #0x8000, %sr
t_trace_insn:
        nop
t_trace_next:
        nop
        rts

| A jump to an odd address: the prefetch there is an address error (UM 8.2.2).
        test    t_addrerr
        lea     t_addrerr_next+1, %a0
t_addrerr_insn:
        jmp     (%a0)
t_addrerr_next:
        rts

| Read and write of an address nothing answers: the bus's time-out, TEA. The
| write is to the I/O region, so the bridge waits for its answer, and its
| error is the write's.
        test    t_berr_read
        move.l  #0x90000000, %a0
t_berr_read_insn:
        move.l  (%a0), %d0
t_berr_read_next:
        rts

        test    t_berr_write
        move.l  #0x90000000, %a0
t_berr_write_insn:
        move.l  %d0, (%a0)
t_berr_write_next:
        nop
        rts

| Long reads and writes at mmu_probe, an address set by main.c, for the MMU
| tests: a fault there is an ATC fault, not a bus error.
        test    t_mmu_read
        move.l  mmu_probe, %a0
t_mmu_read_insn:
        move.l  (%a0), %d0
t_mmu_read_next:
        rts

        test    t_mmu_write
        move.l  mmu_probe, %a0
t_mmu_write_insn:
        move.l  %d0, (%a0)
t_mmu_write_next:
        nop
        rts

| FNOP: on the MC68LC040, an F-line exception with a format $4 frame (UM
| Appendix A); on the MC68040, nothing. As words: the assembler takes no FPU
| instruction in a -msoft-float build.
        test    t_fnop
t_fnop_insn:
        .word   0xf280, 0x0000          | fnop
t_fnop_next:
        rts

#ifdef __HAVE_68881__
| Unimplemented floating-point instructions (UM 9.6.1, Table 9-10), left to
| the M68040FPSP: vector 11, format $2, the PC the next instruction.
        test    t_fsin
        fmove.l #1, %fp0
t_fsin_insn:
        fsin.x  %fp0, %fp0
t_fsin_next:
        rts

        test    t_fmovecr
t_fmovecr_insn:
        fmovecr #0, %fp0
t_fmovecr_next:
        rts

| A divide by zero with its exception enabled (FPCR DZ, UM 9.2.2.1): reported
| as a pre-instruction exception when the next floating-point instruction is
| attempted (UM 9.7.6), a format $0 frame, vector 50, the PC that instruction.
        test    t_fpdz
        fmove.l #0x00000400, %fpcr
        fmove.l #1, %fp0
        fdiv.l  #0, %fp0
t_fpdz_insn:
        fnop
t_fpdz_next:
        rts
#endif

| Last of all, one exception left to crt0's handler, the program's own (VBR is
| back to crt0's table by then). It reports it and stops.
        test    t_unhandled
t_unhandled_insn:
        illegal
t_unhandled_next:
        rts

        .bss
        .even
        .space  256
user_stack_top:
