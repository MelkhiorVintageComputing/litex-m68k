/* SPDX-License-Identifier: BSD-2-Clause */
/* Copyright 2026 Romain Dolbeau */
/* Dump operands and results of the XF routines, weighted to m68k's own
   region: exponent 0 with J set or clear, unnormals, small exponents. */
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include "prelude.h"
XFtype __addxf3(XFtype, XFtype), __subxf3(XFtype, XFtype), __mulxf3(XFtype, XFtype), __divxf3(XFtype, XFtype);
int __lexf2(XFtype, XFtype), __gexf2(XFtype, XFtype), __eqxf2(XFtype, XFtype);
double __truncxfdf2(XFtype);
static uint64_t s = 0x9E3779B97F4A7C15ULL;
static uint64_t rnd(void) { s ^= s << 13; s ^= s >> 7; s ^= s << 17; return s; }
static XFtype gen(void) {
  int k = rnd() % 16; uint64_t m = rnd(); unsigned e;
  switch (k) {
  case 0: e = 0; m |= 1ULL << 63; break;                    /* e=0, J set: normal */
  case 1: e = 0; m &= ~(1ULL << 63); m >>= rnd() % 64; break; /* denormal */
  case 2: e = 1 + rnd() % 70; m &= ~(1ULL << 63); m >>= rnd() % 64; break; /* unnormal */
  case 3: case 4: e = rnd() % 140; m |= 1ULL << 63; break;  /* near the bottom */
  case 5: e = 32700 + rnd() % 67; m |= 1ULL << 63; break;   /* near the top */
  case 6: e = 16383 + rnd() % 130 - 65; m |= 1ULL << 63; break;
  case 7: e = rnd() % 32767; m |= 1ULL << 63; m &= ~((1ULL << (rnd() % 64)) - 1); break;
  default: e = rnd() % 32767; m |= 1ULL << 63; break;
  }
  if (rnd() % 200 == 0) { e = 0; m = 0; }
  XFtype r; r.w[0] = (e | ((rnd() & 1) << 15)) << 16; r.w[1] = m >> 32; r.w[2] = (uint32_t) m; return r;
}
#define P(x) printf(" %04x%08x%08x", (x).w[0] >> 16, (x).w[1], (x).w[2])
int main(int argc, char **argv) {
  long n = atol(argv[1]);
  for (long i = 0; i < n; i++) {
    XFtype a = gen(), b = gen();
    /* one operand from the bottom, the other near 2^k scaling: products land in the denormal range */
    if (i % 4 == 3) { b.w[0] = ((16383 + rnd() % 100 - 50) | ((rnd() & 1) << 15)) << 16; }
    XFtype r[4] = { __addxf3(a, b), __subxf3(a, b), __mulxf3(a, b), __divxf3(a, b) };
    double d = __truncxfdf2(a); uint64_t db; __builtin_memcpy(&db, &d, 8);
    printf("%ld", i); P(a); P(b); for (int j = 0; j < 4; j++) P(r[j]);
    printf(" %d %d %d %016llx\n", __lexf2(a, b), __gexf2(a, b), __eqxf2(a, b), (unsigned long long) db);
  }
}
