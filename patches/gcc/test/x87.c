/* SPDX-License-Identifier: BSD-2-Clause */
/* Copyright 2026 Romain Dolbeau */
/* Host test of libgcc/config/m68k's XF routines (patches/gcc 0003) against x87
   long double, where the two formats agree.  patches/gcc/test/run.sh. */
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <stdlib.h>
#include <math.h>
#include "prelude.h"

XFtype __addxf3(XFtype, XFtype), __subxf3(XFtype, XFtype), __mulxf3(XFtype, XFtype), __divxf3(XFtype, XFtype), __negxf2(XFtype);
int __eqxf2(XFtype, XFtype), __nexf2(XFtype, XFtype), __gexf2(XFtype, XFtype), __gtxf2(XFtype, XFtype), __lexf2(XFtype, XFtype), __ltxf2(XFtype, XFtype), __unordxf2(XFtype, XFtype);
XFtype __extendsfxf2(float), __extenddfxf2(double), __floatsixf(int), __floatunsixf(unsigned);
float __truncxfsf2(XFtype); double __truncxfdf2(XFtype); int __fixxfsi(XFtype);

/* x87 <-> m68k encodings, for x87-normal values, zero, inf, NaN. */
static XFtype to_m(long double x) {
  unsigned char b[16]; memcpy(b, &x, 10); uint64_t m; uint16_t se;
  memcpy(&m, b, 8); memcpy(&se, b + 8, 2);
  if ((se & 0x7fff) == 0 && m) m <<= 1;  /* x87 denormal 0.f*2^-16382 = m68k 0.(2f)*2^-16383 */
  XFtype r; r.w[0] = (unsigned) se << 16; r.w[1] = m >> 32; r.w[2] = (uint32_t) m;
  if ((se & 0x7fff) == 0x7fff && (m << 1) == 0) r.w[1] &= 0x7fffffff; /* inf: J clear */
  return r;
}
static long double to_x(XFtype a) {
  uint16_t se = a.w[0] >> 16; uint64_t m = ((uint64_t) a.w[1] << 32) | a.w[2];
  if ((se & 0x7fff) == 0x7fff) m |= 1ULL << 63; /* x87 wants J set on inf/NaN */
  unsigned char b[16] = {0}; memcpy(b, &m, 8); memcpy(b + 8, &se, 2);
  long double x; memcpy(&x, b, 10); return x;
}
static int m_exp(XFtype a) { return (a.w[0] >> 16) & 0x7fff; }
static int same(long double want, XFtype got) {
  if (isnan(want)) return m_exp(got) == 0x7fff && ((got.w[1] << 1) | got.w[2]);
  XFtype w = to_m(want);
  return !memcmp(&w, &got, sizeof w);
}

static uint64_t s = 88172645463325252ULL;
static uint64_t rnd(void) { s ^= s << 13; s ^= s >> 7; s ^= s << 17; return s; }
static long double rval(int erange) {
  int k = rnd() % 64;
  if (k == 0) return 0.0L * ((rnd() & 1) ? -1 : 1);
  if (k == 1) return ((rnd() & 1) ? -1 : 1) * INFINITY;
  if (k == 2) return NAN;
  uint64_t m = rnd() | (1ULL << 63);
  if (k < 8) m &= ~((1ULL << (rnd() % 64)) - 1); /* short mantissas: exact cases */
  int e = (k < 12) ? 16383 + (int)(rnd() % 40) - 20
        : erange ? 16383 + (int)(rnd() % (2 * erange)) - erange : 1 + rnd() % 32766;
  uint16_t se = e | ((rnd() & 1) << 15);
  unsigned char b[16] = {0}; memcpy(b, &m, 8); memcpy(b + 8, &se, 2);
  long double x; memcpy(&x, b, 10); return x;
}
static int x87_normal(long double x) { return x == 0 || isinf(x) || isnan(x) || fabsl(x) >= 0x1p-16382L; }

int main(int argc, char **argv) {
  long n = argc > 1 ? atol(argv[1]) : 1000000, bad = 0, done = 0;
  for (long i = 0; i < n && bad < 20; i++) {
    int er = (i % 3 == 0) ? 0 : (i % 3 == 1) ? 200 : 16000;  /* any exponent, near 1.0, wide */
    long double a = rval(er), b = rval(er);
    XFtype A = to_m(a), B = to_m(b);
    struct { const char *n; long double w; XFtype g; } t[] = {
      {"add", a + b, __addxf3(A, B)}, {"sub", a - b, __subxf3(A, B)},
      {"mul", a * b, __mulxf3(A, B)}, {"div", a / b, __divxf3(A, B)},
      {"neg", -a, __negxf2(A)} };
    for (unsigned j = 0; j < sizeof t / sizeof t[0]; j++) {
      if (!x87_normal(t[j].w)) continue; /* m68k's extra binade: not comparable */
      if (t[j].w == 0 && j >= 2 && j <= 3 && a != 0 && b != 0 && isfinite(a) && isfinite(b))
        continue; /* an x87 underflow to zero: m68k's denormals go one binade lower */
      done++;
      if (!same(t[j].w, t[j].g)) {
        bad++; printf("%s(%La, %La): want %La got %La [%08x %08x %08x]\n", t[j].n, a, b, t[j].w, to_x(t[j].g), t[j].g.w[0], t[j].g.w[1], t[j].g.w[2]);
      }
    }
    int un = isnan(a) || isnan(b);
    if ((__eqxf2(A, B) == 0) != (a == b) || (__nexf2(A, B) != 0) != (a != b) ||
        (__gexf2(A, B) >= 0) != (a >= b) || (__gtxf2(A, B) > 0) != (a > b) ||
        (__lexf2(A, B) <= 0) != (a <= b) || (__ltxf2(A, B) < 0) != (a < b) ||
        (__unordxf2(A, B) != 0) != un) { bad++; printf("cmp(%La, %La)\n", a, b); }
    double d; memcpy(&d, &(uint64_t){rnd()}, 8); float f; memcpy(&f, &(uint32_t){(uint32_t) rnd()}, 4);
    if (!same((long double) d, __extenddfxf2(d))) { bad++; printf("extenddf(%a)\n", d); }
    if (!same((long double) f, __extendsfxf2(f))) { bad++; printf("extendsf(%a)\n", f); }
    double td = (double) a, gd = __truncxfdf2(A); float tf = (float) a, gf = __truncxfsf2(A);
    if (!(isnan(td) ? isnan(gd) : !memcmp(&td, &gd, 8))) { bad++; printf("truncdf(%La): want %a got %a\n", a, td, gd); }
    if (!(isnan(tf) ? isnan(gf) : !memcmp(&tf, &gf, 4))) { bad++; printf("truncsf(%La): want %a got %a\n", a, tf, gf); }
    int k = (int) rnd(); unsigned u = (unsigned) rnd();
    if (!same((long double) k, __floatsixf(k)) || !same((long double) u, __floatunsixf(u))) { bad++; printf("float(%d,%u)\n", k, u); }
    if (!isnan(a) && fabsl(a) < 2147483647.0L && __fixxfsi(A) != (int) a) { bad++; printf("fix(%La)\n", a); }
  }
  printf("%ld cases, %ld arithmetic results compared, %ld wrong\n", n, done, bad);
  return bad != 0;
}
