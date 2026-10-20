/* SPDX-License-Identifier: BSD-2-Clause
 * Copyright 2026 Romain Dolbeau
 *
 * L2 smoke test link map, for a LiteX SoC with RD68031 (litex_68k/cpu/rd68031):
 * the program in the ROM at 0, where the reset vectors are read (UM 8.1.1), its
 * variables and its stack in the SRAM. No .data: nothing copies it.
 */

ENTRY(_start)

MEMORY
{
  rom  (rx)  : ORIGIN = 0x00000000, LENGTH = 0x00020000
  sram (rw)  : ORIGIN = 0x01000000, LENGTH = 0x00002000
}

SECTIONS
{
  .vectors 0x00000000 : { KEEP(*(.vectors)) } > rom
  .text :
  {
    *(.text.startup)
    *(.text*)
    *(.rodata*)
  } > rom
  .bss (NOLOAD) : { __bss_start = .; *(.bss*) *(COMMON) . = ALIGN(4); __bss_end = .; } > sram

  __stack_top = ORIGIN(sram) + LENGTH(sram);

  ASSERT(SIZEOF(.data) == 0, "no .data: nothing copies it to the SRAM")
  /DISCARD/ : { *(.comment) *(.note*) *(.eh_frame*) }
}
