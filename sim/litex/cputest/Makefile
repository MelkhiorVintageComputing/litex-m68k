# SPDX-License-Identifier: BSD-2-Clause
# Copyright 2026 Romain Dolbeau

# cputest, built against a LiteX SoC's software the way LiteX's demo is:
#
#   make -f <this> BUILD_DIR=<the SoC's output directory>
#
# run from the directory the objects and cputest.bin go to (l4_test.py does).

SRC := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))

include $(BUILD_DIR)/software/include/generated/variables.mak
include $(SOC_DIRECTORY)/software/common.mak

OBJECTS = crt0.o traps.o main.o

all: cputest.bin

cputest.bin: cputest.elf
	$(OBJCOPY) -O binary $< $@

cputest.elf: $(OBJECTS) $(SRC)linker.ld
	$(CC) $(LDFLAGS) -T $(SRC)linker.ld -N -o $@ $(OBJECTS) \
		-L$(BUILD_DIR)/software/libc -L$(BUILD_DIR)/software/libbase \
		-L$(BUILD_DIR)/software/libcompiler_rt \
		-Wl,--start-group -lc -lbase -lcompiler_rt -Wl,--end-group \
		-Wl,--gc-sections,--no-warn-rwx-segments -Wl,-Map,$@.map

-include $(OBJECTS:.o=.d)

VPATH = $(SRC):$(CPU_DIRECTORY)

%.o: %.c
	$(compile)

%.o: %.S
	$(assemble)

.PHONY: all
