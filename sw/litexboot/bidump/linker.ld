/* bidump: run at main RAM's start, where litexboot copies a kernel. litexboot
 * places each segment at main RAM + its physical address and enters at main
 * RAM + e_entry, so both are offsets; the addresses are main RAM's. */
OUTPUT_FORMAT("elf32-m68k")
ENTRY(_entry_offset)

MAIN_RAM = 0x40000000;
LOAD     = 0x1000;

SECTIONS
{
	. = MAIN_RAM + LOAD;
	.text : AT(LOAD) { *(.text.start) *(.text .text.*) *(.rodata .rodata.*) }
	.data : { *(.data .data.*) }
	.bss  : { *(.bss .bss.*) *(COMMON) }
	. = ALIGN(4);
	_end = .;
	_entry_offset = _start - MAIN_RAM;
	/DISCARD/ : { *(.comment) *(.note*) }
}
