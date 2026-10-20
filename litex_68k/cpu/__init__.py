# SPDX-License-Identifier: BSD-2-Clause
# Copyright 2026 Romain Dolbeau

"""The m68k CPUs, registered with LiteX without changing it.

LiteX finds its CPUs once, at import, in its own cpu/ directory and in the
current directory (litex/soc/cores/cpu/__init__.py, collect_cpus). register()
adds ours to the same table, so --cpu-type and SoC.add_cpu() accept them. Call
it before LiteX builds its argument parser.
"""

def register():
    from litex.soc.cores import cpu
    from litex_68k.cpu.rd68031 import RD68031
    cpu.CPUS.setdefault("rd68031", RD68031)
