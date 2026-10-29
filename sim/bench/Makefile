# SPDX-License-Identifier: BSD-2-Clause
# Copyright 2026 Romain Dolbeau

# L1 bench: RD68031 + M68030ToWishbone, no LiteX SoC (doc/plan.md, L1).
#
#   make check          every program
#   make run PROG=x     one program (bustest, arith)
#   make CACHES=0 ...   the core built with no caches (ICACHE/DCACHE_ENTRIES=0)
#   make BRIDGE=fifo ...  M68030ToFIFOToSoC, the processor on a clock of its own
#                       (PLUSARGS=+cpu_half=<ns> sets it; sys's is 5)
#   make CPU=rd68041 ...  RD68041 (an MC68040) behind M68040ToFIFOToSoC, on a
#                       clock of its own as with BRIDGE=fifo; FPU=0 for the
#                       MC68LC040 (L11, programs040/)
#   make CPU=rd68041 SMP=1 check  two RD68041s on one bus (L19,
#                       litex_68k/m68040_smp.py) running RD68041's own
#                       sim/programs/m14_smp.S, ITERS=6 by default, with
#                       setup A's policy (WTSHARED=1)
#   make CPU=rd68041 SMP=1 smp-negative  the runs that must fail: no MI gate
#                       (a hang), UMASK=0xfffffcff (no snooping), the printed arbiter
#                       (its handover with no clock between seen)
#
# Needs the venv (scripts/setup_venv.sh) and ../pythondata-cpu-rd68031
# (scripts/sync_pythondata.sh), or ../pythondata-cpu-rd68041
# (scripts/sync_pythondata_rd68041.sh). The Verilator build of the core is a
# heavy job: run it alone (CLAUDE.md).

TOP     := $(abspath ../..)
CROSS   ?= m68k-linux-gnu-
PYTHON  ?= $(HOME)/LITEX-68K/litex-venv/bin/python
CPU     ?= rd68031
CACHES  ?= 64
BRIDGE  ?= wishbone
FPU     ?= 1

ifeq ($(CPU),rd68041)
BUILD   := build/rd68041-fpu$(FPU)
PROGDIR := build/programs040
PROGSRC := programs040
CORE_V  := $(abspath $(TOP)/../pythondata-cpu-rd68041/pythondata_cpu_rd68041/verilog)
CORE_SRCS := $(addprefix $(CORE_V)/,$(shell cat $(CORE_V)/rd68041.f))
CORE_VLT  := $(CORE_V)/rtl/rd68041.vlt
BRIDGE_SRCS := $(TOP)/litex_68k/m68040_bus.py $(TOP)/litex_68k/m68k_fifo.py $(TOP)/litex_68k/cpu/rd68031/core.py
BENCH_ARGS  := --cpu rd68041 --fpu $(FPU)
VDEFS   := -DFIFO_BRIDGE -DRD68041
MCPU    := -mcpu=68040 -msoft-float
ifeq ($(SMP),1)
BUILD   := $(BUILD)-smp$(if $(filter 1,$(NOMIGATE)),-nomigate)$(if $(filter 1,$(GAPLESS)),-gapless)
BENCH_ARGS += --smp $(if $(filter 1,$(NOMIGATE)),--no-mi-gate) $(if $(filter 1,$(GAPLESS)),--gapless)
BRIDGE_SRCS += $(TOP)/litex_68k/m68040_smp.py
VDEFS   += -DSMP
endif
else
BUILD   := build/$(BRIDGE)-caches$(CACHES)
PROGDIR := build/programs
PROGSRC := programs
CORE_V  := $(abspath $(TOP)/../pythondata-cpu-rd68031/pythondata_cpu_rd68031/verilog)
CORE_SRCS := $(addprefix $(CORE_V)/,$(shell cat $(CORE_V)/rd68031.f))
CORE_VLT  := $(CORE_V)/rtl/rd68031.vlt
BRIDGE_SRCS := $(TOP)/litex_68k/m68030_bus.py $(TOP)/litex_68k/m68030_fifo.py $(TOP)/litex_68k/m68k_fifo.py $(TOP)/litex_68k/cpu/rd68031/core.py
BENCH_ARGS  := --icache $(CACHES) --dcache $(CACHES) --bridge $(BRIDGE)
VDEFS   := $(if $(filter fifo,$(BRIDGE)),-DFIFO_BRIDGE)
MCPU    := -mcpu=68030
endif
CONFIG  := $(if $(filter rd68041,$(CPU)),rd68041 fpu=$(FPU)$(if $(filter 1,$(SMP)), smp),$(BRIDGE) caches=$(CACHES))
SMP      ?= 0
ITERS    ?= 6
UMASK    ?= 0xffffffff
WTSHARED ?= 1
SMP_MAX  ?= 50000000

# Programs, and the RESULT each must write.
PROGS          := bustest arith
EXPECT_bustest := $(if $(filter rd68041,$(CPU)),0040600d,00000000)
# arith.c's main() as a 32-bit x86 build computes it, with no m68k in the loop:
# see doc/l1-bench.md.
EXPECT_arith   := 688cea90

CFLAGS  := $(MCPU) -O2 -ffreestanding -fno-builtin -fomit-frame-pointer
ASFLAGS := $(MCPU) -I$(PROGSRC)
LDFLAGS := -nostdlib -nostartfiles -Wl,--build-id=none,--no-warn-rwx-segments -T programs/bench.ld

.PHONY: check run clean smp-run smp-negative
.SECONDARY:

ifeq ($(SMP),1)
# RD68041's two-processor program, from Inputs/ as it is, built as RD68041
# builds its programs (flat at 0, sim/programs/flat.ld), then its parameters
# poked in (smp_image.py).
SMP_SRC := $(TOP)/Inputs/RD68041/sim/programs
SMP_HEX := $(PROGDIR)/m14_smp-$(ITERS)-$(UMASK)-$(WTSHARED).hex
$(PROGDIR)/m14_smp.bin: $(SMP_SRC)/m14_smp.S $(SMP_SRC)/flat.ld
	@mkdir -p $(PROGDIR)
	$(CROSS)gcc -mcpu=68040 -c -o $(PROGDIR)/m14_smp.o $<
	$(CROSS)gcc -mcpu=68040 -nostdlib -nostartfiles -T $(SMP_SRC)/flat.ld \
	    -Wl,--no-warn-rwx-segments -o $(PROGDIR)/m14_smp.elf $(PROGDIR)/m14_smp.o
	$(CROSS)objcopy -O binary $(PROGDIR)/m14_smp.elf $@
$(SMP_HEX): $(PROGDIR)/m14_smp.bin smp_image.py
	$(PYTHON) smp_image.py $< $@ --iters $(ITERS) --umask $(UMASK) --wtshared $(WTSHARED)

smp-run: $(BUILD)/Vtb $(SMP_HEX)
	$(BUILD)/Vtb +ram=$(SMP_HEX) +iters=$(ITERS) +max_cycles=$(SMP_MAX) $(PLUSARGS)

check: $(BUILD)/Vtb $(SMP_HEX)
	@printf '%-8s %-22s ' m14_smp "$(CONFIG)"; \
	if $(MAKE) --no-print-directory -s smp-run > $(BUILD)/m14_smp.log 2>&1; \
	then grep -h '^DONE' $(BUILD)/m14_smp.log | sed 's/^DONE/PASS,/'; \
	else echo "FAIL (see sim/bench/$(BUILD)/m14_smp.log)"; exit 1; fi

# The negative runs: each must fail as doc/smp.md predicts (a heavy job each:
# the no-MI-gate and gapless benches are Verilator builds of their own).
smp-negative:
	@printf '%-26s ' "no MI gate (must hang)"; \
	if $(MAKE) --no-print-directory -s NOMIGATE=1 SMP_MAX=3000000 smp-run > build/neg-nomigate.log 2>&1; \
	then echo "FAIL: it passed"; exit 1; else grep -h 'FAIL' build/neg-nomigate.log | tail -1; fi
	@printf '%-26s ' "no snooping (must fail)"; \
	if $(MAKE) --no-print-directory -s UMASK=0xfffffcff SMP_MAX=3000000 smp-run > build/neg-umask.log 2>&1; \
	then echo "FAIL: it passed"; exit 1; else grep -h 'FAIL' build/neg-umask.log | tail -1; fi
	@printf '%-26s ' "printed arbiter (caught)"; \
	if $(MAKE) --no-print-directory -s GAPLESS=1 PLUSARGS=+gapless SMP_MAX=3000000 smp-run > build/neg-gapless.log 2>&1; \
	then grep -h '^PASS' build/neg-gapless.log; else echo "FAIL (see sim/bench/build/neg-gapless.log)"; exit 1; fi
else
check: $(BUILD)/Vtb $(addprefix $(PROGDIR)/,$(addsuffix .hex,$(PROGS)))
	@fail=0; for p in $(PROGS); do \
	    printf '%-8s %-22s ' $$p "$(CONFIG)"; \
	    if $(MAKE) --no-print-directory -s run PROG=$$p > $(BUILD)/$$p.log 2>&1; \
	    then grep -h '^EXIT' $(BUILD)/$$p.log | sed 's/^EXIT [0-9a-f]* after/PASS,/'; \
	    else echo "FAIL (see sim/bench/$(BUILD)/$$p.log)"; fail=1; fi; \
	done; exit $$fail

endif

run: $(BUILD)/Vtb $(PROGDIR)/$(PROG).hex
	$(BUILD)/Vtb +ram=$(PROGDIR)/$(PROG).hex +expect=$(EXPECT_$(PROG)) $(PLUSARGS)

# The bench's Verilog, then the simulator.
$(BUILD)/bench.v: bench.py $(BRIDGE_SRCS)
	@mkdir -p $(BUILD)
	$(PYTHON) bench.py --output $@ $(BENCH_ARGS)

$(BUILD)/Vtb: tb.sv $(BUILD)/bench.v $(CORE_SRCS)
	verilator --binary --timing -j 4 -Wno-fatal -Wno-lint -Wno-style $(VDEFS) \
	    --top-module tb --Mdir $(BUILD)/obj -o ../Vtb \
	    $(CORE_VLT) $(CORE_SRCS) $(BUILD)/bench.v tb.sv \
	    > $(BUILD)/verilator.log 2>&1 || { tail -30 $(BUILD)/verilator.log; exit 1; }

# Programs: an ELF, then one big-endian 32-bit word per line for $readmemh.
$(PROGDIR)/%.o: $(PROGSRC)/%.S $(PROGSRC)/bench.inc
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
