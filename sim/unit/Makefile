# SPDX-License-Identifier: BSD-2-Clause
# Copyright 2026 Romain Dolbeau

# Unit tests of core.py's bus pieces, in migen's simulator (seconds, not a
# Verilator build): WishboneSyncCrossing with two clocks, WishboneErrorTimeout.
# L18, the two-processor board (litex_68k/m68040_smp.py): the arbiter against
# its table, and against RD68041's rd68041_fairarb.sv under iverilog; the
# devices and the MI gate.

PYTHON ?= $(HOME)/LITEX-68K/litex-venv/bin/python

.PHONY: check
check:
	cd ../.. && $(PYTHON) sim/unit/sync_crossing.py
	cd ../.. && $(PYTHON) sim/unit/sync_crossing_abandon.py
	cd ../.. && $(PYTHON) sim/unit/error_timeout.py
	cd ../.. && $(PYTHON) sim/unit/smp_arbiter.py
	cd ../.. && $(PYTHON) sim/unit/smp_arbiter_equiv.py
	cd ../.. && $(PYTHON) sim/unit/smp_devices.py
