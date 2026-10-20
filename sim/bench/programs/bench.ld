/* SPDX-License-Identifier: BSD-2-Clause
 * Copyright 2026 Romain Dolbeau
 *
 * L1 bench link map: 256 KB of RAM at 0 (sim/bench/bench.py), the vector table
 * at 0 (VBR is 0 after reset), the program after it, the stack at the top. The
 * image is the memory: tb.sv loads it whole.
 */

ENTRY(_start)

MEMORY
{
  ram (rwx) : ORIGIN = 0x00000000, LENGTH = 0x00040000
}

SECTIONS
{
  .vectors 0x00000000 : { KEEP(*(.vectors)) } > ram

  .text 0x00000400 :
  {
    *(.text.startup)
    *(.text*)
    *(.rodata*)
  } > ram

  .data : { *(.data*) } > ram
  .bss  : { __bss_start = .; *(.bss*) *(COMMON) . = ALIGN(4); __bss_end = .; } > ram

  _end = .;
  __stack_top = ORIGIN(ram) + LENGTH(ram);

  /DISCARD/ : { *(.comment) *(.note*) *(.eh_frame*) }
}
