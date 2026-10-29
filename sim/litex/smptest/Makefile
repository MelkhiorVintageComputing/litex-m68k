# SPDX-License-Identifier: BSD-2-Clause
# Copyright 2026 Romain Dolbeau

# smptest (L20): two RD68041s ("+smp"), built against a LiteX SoC's software
# the way cputest040 is:
#
#   make -f <this> BUILD_DIR=<the SoC's output directory> [ITERS=n]
#
# run from the directory the objects and smptest.bin go to (smp_test.py does).

SRC := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))

include $(BUILD_DIR)/software/include/generated/variables.mak
include $(SOC_DIRECTORY)/software/common.mak

ITERS ?= 16
CFLAGS += -DITERS=$(ITERS) $(SMPTEST_CFLAGS)

OBJECTS = crt0.o smp.o main.o

all: smptest.bin

smptest.bin: smptest.elf
	$(OBJCOPY) -O binary $< $@

smptest.elf: $(OBJECTS) $(SRC)../cputest/linker.ld
	$(CC) $(LDFLAGS) -T $(SRC)../cputest/linker.ld -N -o $@ $(OBJECTS) \
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
