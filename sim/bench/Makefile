# SPDX-License-Identifier: BSD-2-Clause
# Copyright 2026 Romain Dolbeau

# L1 bench: RD68031 + M68030ToWishbone, no LiteX SoC (doc/plan.md, L1).
#
#   make check          every program, both cache configurations
#   make run PROG=x     one program (bustest, arith)
#   make CACHES=0 ...   the core built with no caches (ICACHE/DCACHE_ENTRIES=0)
#   make BRIDGE=fifo ...  M68030ToFIFOToSoC, the processor on a clock of its own
#                       (PLUSARGS=+cpu_half=<ns> sets it; sys's is 5)
#
# Needs the venv (scripts/setup_venv.sh) and ../pythondata-cpu-rd68031
# (scripts/sync_pythondata.sh). The Verilator build of the core is a heavy job:
# run it alone (CLAUDE.md).

TOP     := $(abspath ../..)
CROSS   ?= m68k-linux-gnu-
PYTHON  ?= $(HOME)/LITEX-68K/litex-venv/bin/python
CACHES  ?= 64
BRIDGE  ?= wishbone
BUILD   := build/$(BRIDGE)-caches$(CACHES)
PROGDIR := build/programs

RD68031_V := $(abspath $(TOP)/../pythondata-cpu-rd68031/pythondata_cpu_rd68031/verilog)
RD68031_SRCS := $(addprefix $(RD68031_V)/,$(shell cat $(RD68031_V)/rd68031.f))
BRIDGE_SRCS := $(TOP)/litex_68k/m68030_bus.py $(TOP)/litex_68k/m68030_fifo.py $(TOP)/litex_68k/cpu/rd68031/core.py
VDEFS   := $(if $(filter fifo,$(BRIDGE)),-DFIFO_BRIDGE)

# Programs, and the RESULT each must write.
PROGS          := bustest arith
EXPECT_bustest := 00000000
# arith.c's main() as a 32-bit x86 build computes it, with no m68k in the loop:
# see doc/l1-bench.md.
EXPECT_arith   := 688cea90

CFLAGS  := -mcpu=68030 -O2 -ffreestanding -fno-builtin -fomit-frame-pointer
ASFLAGS := -mcpu=68030 -Iprograms
LDFLAGS := -nostdlib -nostartfiles -Wl,--build-id=none,--no-warn-rwx-segments -T programs/bench.ld

.PHONY: check run clean
.SECONDARY:

check: $(BUILD)/Vtb $(addprefix $(PROGDIR)/,$(addsuffix .hex,$(PROGS)))
	@fail=0; for p in $(PROGS); do \
	    printf '%-8s %s caches=%-3s ' $$p $(BRIDGE) $(CACHES); \
	    if $(MAKE) --no-print-directory -s run PROG=$$p > $(BUILD)/$$p.log 2>&1; \
	    then grep -h '^EXIT' $(BUILD)/$$p.log | sed 's/^EXIT [0-9a-f]* after/PASS,/'; \
	    else echo "FAIL (see sim/bench/$(BUILD)/$$p.log)"; fail=1; fi; \
	done; exit $$fail

run: $(BUILD)/Vtb $(PROGDIR)/$(PROG).hex
	$(BUILD)/Vtb +ram=$(PROGDIR)/$(PROG).hex +expect=$(EXPECT_$(PROG)) $(PLUSARGS)

# The bench's Verilog, then the simulator.
$(BUILD)/bench.v: bench.py $(BRIDGE_SRCS)
	@mkdir -p $(BUILD)
	$(PYTHON) bench.py --output $@ --icache $(CACHES) --dcache $(CACHES) --bridge $(BRIDGE)

$(BUILD)/Vtb: tb.sv $(BUILD)/bench.v $(RD68031_SRCS)
	verilator --binary --timing -j 4 -Wno-fatal -Wno-lint -Wno-style $(VDEFS) \
	    --top-module tb --Mdir $(BUILD)/obj -o ../Vtb \
	    $(RD68031_V)/rtl/rd68031.vlt $(RD68031_SRCS) $(BUILD)/bench.v tb.sv \
	    > $(BUILD)/verilator.log 2>&1 || { tail -30 $(BUILD)/verilator.log; exit 1; }

# Programs: an ELF, then one big-endian 32-bit word per line for $readmemh.
$(PROGDIR)/%.o: programs/%.S programs/bench.inc
	@mkdir -p $(PROGDIR)
	$(CROSS)gcc $(ASFLAGS) -c -o $@ $<

$(PROGDIR)/arith.o: $(TOP)/Inputs/RD68031/sim/programs/arith.c
	@mkdir -p $(PROGDIR)
	$(CROSS)gcc $(CFLAGS) -c -o $@ $<

$(PROGDIR)/%.elf: $(PROGDIR)/crt0.o $(PROGDIR)/%.o programs/bench.ld
	$(CROSS)gcc $(LDFLAGS) -o $@ $(PROGDIR)/crt0.o $(PROGDIR)/$*.o -lgcc

$(PROGDIR)/%.hex: $(PROGDIR)/%.elf
	$(CROSS)objcopy -O binary $< $(PROGDIR)/$*.bin
	od -An -v -tx4 -w4 --endian=big $(PROGDIR)/$*.bin | tr -d ' ' > $@

clean:
	rm -rf build
