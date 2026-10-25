# RD68041 patches

Against RD68041 `0c07e748e6599b6dd7dc93395540edb657ddc496` (`Inputs/RD68041`,
"M14: NetBSD and Linux on QEMU's virt with the core as its processor").
`scripts/patch_rd68041.sh` applies them to a copy, `~/LITEX-68K/RD68041`, and
`scripts/sync_pythondata_rd68041.sh` takes the RTL from it.

## 0001-M14-instruction-fetch-stops-at-an-ATC-fault-leaving-.patch

- **What:** `rtl/rd68041_ifu.sv` stops fetching after an instruction fetch's ATC
  fault, until the next redirect (`xstop`). Before, it went on to the next page,
  searched the tables for it, and left a not-resident ATC entry for that page.
  - Also adds `sim/programs/m15_fetch_next.S` and `sim/tb/core_fetchnext_tb.sv`,
    the regression, and an entry in `doc/bugs-found.md`.
- **Why:** Linux's 040 fault handler maps the faulting page and, by
  fault-around, its neighbours, but flushes only the faulting page's ATC entry.
  - The fetch after its RTE then faulted on the stale entry for the next page.
  - Linux's PTEST found that page resident, so it took the fault for a
    protection violation and killed the process.
  - With buildroot's glibc userspace, init died of SIGSEGV on its first
    `fopen64` (a `MOVE.L` across two pages), and the kernel panicked.
  - Found in litex_sim with a user-mode trace (`sim/litex/trace/`), and on the
    Wukong; `doc/l11-rd68041.md` has the investigation.
- **Checked:**
  - RD68041's `make check`: ucode-check, lint, audit, its 18 testbenches,
    timing. `core_fetchnext_tb` takes two faults without the change, one with
    it.
  - Then Linux on RD68041 (L15).
- **Upstream:** not sent. A sign-off is needed.
