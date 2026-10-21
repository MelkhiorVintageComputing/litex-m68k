/* SPDX-License-Identifier: BSD-2-Clause */
/* Copyright 2026 Romain Dolbeau */
/* Host test (run.sh): the m68k description, with the host's byte order
   for the SFmode and DFmode it converts from and to.  */
#include "config/m68k/sfp-machine.h"
#undef __BYTE_ORDER
#define __BYTE_ORDER __LITTLE_ENDIAN
