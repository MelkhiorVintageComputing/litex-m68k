/* SPDX-License-Identifier: BSD-2-Clause */
/* Copyright 2026 Romain Dolbeau */

/* cputest: everything in main_ram, where the BIOS loads it, with the stack
 * at its top. The BIOS's own .data, .bss and stack stay in sram, untouched
 * (crt0 never copies .data: the image already has it where it runs). */

INCLUDE generated/output_format.ld
ENTRY(_start)

INCLUDE generated/regions.ld

SECTIONS
{
	.text :
	{
		*crt0*(.text)           /* the vector table first: crt0.S */
		KEEP(*crt0*(.text))
		KEEP(*(.text.isr))
		*(.text .text.*)
	} > main_ram

	.rodata : { . = ALIGN(8); *(.rodata .rodata.*) } > main_ram

	/* For litex_startup_init (libbase), which runs these; there are none. */
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
