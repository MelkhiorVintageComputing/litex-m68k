# SPDX-License-Identifier: BSD-2-Clause
# Copyright 2026 Romain Dolbeau

# Unit tests of core.py's bus pieces, in migen's simulator (seconds, not a
# Verilator build): WishboneSyncCrossing with two clocks, WishboneErrorTimeout.

PYTHON ?= $(HOME)/LITEX-68K/litex-venv/bin/python

.PHONY: check
check:
	cd ../.. && $(PYTHON) sim/unit/sync_crossing.py
	cd ../.. && $(PYTHON) sim/unit/sync_crossing_abandon.py
	cd ../.. && $(PYTHON) sim/unit/error_timeout.py
