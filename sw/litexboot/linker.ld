/* SPDX-License-Identifier: BSD-2-Clause */
/* Copyright 2026 Romain Dolbeau */

/* litexboot: 1 MiB below the top of main RAM (layout.h), its stack at the
 * top; the kernel goes at the bottom, its files in between. */

INCLUDE generated/output_format.ld
ENTRY(_start)

INCLUDE generated/regions.ld

SECTIONS
{
	.text ORIGIN(main_ram) + LENGTH(main_ram) - 0x100000 :
	{
		*crt0*(.text)           /* the vector table first: crt0.S */
		KEEP(*crt0*(.text))
		KEEP(*(.text.isr))
		*(.text .text.*)
	} > main_ram

	.rodata : { . = ALIGN(8); *(.rodata .rodata.*) } > main_ram

	.init_array :
	{
		. = ALIGN(4);
		__preinit_array_start = .;
		KEEP(*(.preinit_array))
		__preinit_array_end = .;
		__init_array_start = .;
		KEEP(*(SORT(.init_array.*)))
		KEEP(*(.init_array))
		__init_array_end = .;
	} > main_ram

	.data :
	{
		. = ALIGN(8);
		_fdata = .;
		*(.data .data.*)
		. = ALIGN(8);
		_edata = .;
	} > main_ram

	.bss (NOLOAD) :
	{
		. = ALIGN(8);
		_fbss = .;
		*(.bss .bss.* COMMON)
		. = ALIGN(8);
		_ebss = .;
	} > main_ram
}

PROVIDE(_fstack = ORIGIN(main_ram) + LENGTH(main_ram));
PROVIDE(_fdata_rom = _fdata);
