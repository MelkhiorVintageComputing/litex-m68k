# SPDX-License-Identifier: BSD-2-Clause
# Copyright 2026 Romain Dolbeau

# L2: RD68031 in a LiteX SoC under litex_sim, before the BIOS (doc/plan.md).
#
#   make check     build the SoC's headers, hello.c against them, then run it from
#                  the ROM: passes on "L2 PASS"
#   make bios      L3: the LiteX BIOS, driven over the UART (bios_test.py)
#
# The Verilator build of the SoC is a heavy job: run it alone (CLAUDE.md).

TOP     := $(abspath ../..)
CROSS   ?= m68k-linux-gnu-
PYTHON  ?= $(HOME)/LITEX-68K/litex-venv/bin/python
VARIANT ?= standard
BUILD   := build/$(VARIANT)

LITEX_SW := $(shell $(PYTHON) -c 'import litex, os; print(os.path.join(os.path.dirname(litex.__file__), "soc", "software"))')
CPU_DIR  := $(TOP)/litex_68k/cpu/rd68031

SIM := $(PYTHON) $(TOP)/targets/sim.py --cpu-variant=$(VARIANT) --output-dir $(BUILD)/soc \
       --integrated-main-ram-size=0x10000 --sim-debug --no-compile-software

CFLAGS := -mcpu=68030 -O2 -ffreestanding -fno-builtin -fomit-frame-pointer \
          -I$(LITEX_SW)/include -I$(BUILD)/soc/software/include -I$(CPU_DIR)
LDFLAGS := -nostdlib -nostartfiles -Wl,--build-id=none,--no-warn-rwx-segments -T rom.ld

.PHONY: check bios headers clean

check: $(BUILD)/hello.bin
	$(SIM) --rom-init $(BUILD)/hello.bin --non-interactive > $(BUILD)/run.log 2>&1 || true
	@grep -a -A12 'Hello from RD68031' $(BUILD)/run.log || { tail -30 $(BUILD)/run.log; exit 1; }
	@grep -a -q '^L2 PASS' $(BUILD)/run.log

# The SoC's generated headers (csr.h, soc.h, mem.h), without building anything.
headers: $(BUILD)/soc/software/include/generated/csr.h
$(BUILD)/soc/software/include/generated/csr.h: $(TOP)/litex_68k/cpu/rd68031/core.py $(TOP)/litex_68k/m68030_bus.py
	@mkdir -p $(BUILD)
	$(SIM) --no-compile-gateware > $(BUILD)/headers.log 2>&1 || { tail -30 $(BUILD)/headers.log; exit 1; }

$(BUILD)/hello.elf: crt0_rom.S hello.c rom.ld $(BUILD)/soc/software/include/generated/csr.h $(CPU_DIR)/irq.h $(CPU_DIR)/system.h
	$(CROSS)gcc $(CFLAGS) $(LDFLAGS) -o $@ crt0_rom.S hello.c -lgcc

$(BUILD)/hello.bin: $(BUILD)/hello.elf
	$(CROSS)objcopy -O binary $< $@

bios:
	$(PYTHON) -u bios_test.py --variant $(VARIANT)

clean:
	rm -rf build
