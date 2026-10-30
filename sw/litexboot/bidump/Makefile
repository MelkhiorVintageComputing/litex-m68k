# SPDX-License-Identifier: BSD-2-Clause
# Copyright 2026 Romain Dolbeau

# bidump, a test kernel for litexboot (bidump.c), built against a LiteX SoC's
# software as litexboot is:
#
#   make -f <this> BUILD_DIR=<the SoC's output directory, or a bitstream snapshot>

SRC := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))

include $(BUILD_DIR)/software/include/generated/variables.mak
include $(SOC_DIRECTORY)/software/common.mak

OBJECTS = start.o bidump.o

all: bidump.elf

bidump.elf: $(OBJECTS) $(SRC)linker.ld
	$(CC) $(LDFLAGS) -nostdlib -T $(SRC)linker.ld -N -o $@ $(OBJECTS) -Wl,--no-warn-rwx-segments

-include $(OBJECTS:.o=.d)

VPATH = $(SRC)

%.o: %.c
	$(compile)

%.o: %.S
	$(assemble)

.PHONY: all
