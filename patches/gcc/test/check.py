#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright 2026 Romain Dolbeau
"""Exact reference for the m68k extended routines: decode, compute with
Fractions, round to nearest even on m68k's grid, compare."""
import sys
from fractions import Fraction as F

def dec(h):
    se, m = int(h[:4], 16), int(h[4:], 16)
    s, e = se >> 15, se & 0x7fff
    if e == 0x7fff:
        return ("nan" if m & ((1 << 63) - 1) else "inf", s)
    return (F(m) * F(2) ** (e - 16383 - 63), s)

def ilog2(q):  # floor(log2(q)), q > 0
    e = q.numerator.bit_length() - q.denominator.bit_length()
    if F(2) ** e > q: e -= 1
    if F(2) ** (e + 1) <= q: e += 1
    return e

def rnd_even(q):
    n = q.numerator // q.denominator; r = q - n
    if r > F(1, 2) or (r == F(1, 2) and n & 1): n += 1
    return n

def enc(v, s):
    if v == "inf": return "%04x%016x" % ((s << 15) | 0x7fff, 0)
    if v == 0: return "%04x%016x" % (s << 15, 0)
    E = max(ilog2(v), -16383)
    M = rnd_even(v / F(2) ** (E - 63))
    if M == 1 << 64: M >>= 1; E += 1
    if E > 16383: return enc("inf", s)
    e = E + 16383 if M >> 63 else 0
    return "%04x%016x" % ((s << 15) | e, M)

def val(x):  # signed value or special
    v, s = x
    if v in ("nan", "inf"): return x
    return -v if s else v

def op(o, a, b):
    (av, as_), (bv, bs) = a, b
    if av == "nan" or bv == "nan": return "nan"
    A, B = val(a), val(b)
    if o in "+-":
        if o == "-": B = ("inf", 1 - bs) if bv == "inf" else -B; bs2 = 1 - bs
        else: bs2 = bs
        if av == "inf" and bv == "inf": return ("inf", as_) if as_ == bs2 else "nan"
        if av == "inf": return ("inf", as_)
        if bv == "inf": return ("inf", bs2)
        r = A + B
        if r == 0: return (0, as_ if (av == 0 and bv == 0 and as_ == bs2) else 0)
        return (abs(r), int(r < 0))
    s = as_ ^ bs
    if o == "*":
        if (av == "inf" and bv == 0) or (bv == "inf" and av == 0): return "nan"
        if "inf" in (av, bv): return ("inf", s)
        return (abs(A * B), s)
    if av == "inf" and bv == "inf": return "nan"
    if av == 0 and bv == 0: return "nan"
    if av == "inf": return ("inf", s)
    if bv == "inf": return (0, s)
    if bv == 0: return ("inf", s)
    return (abs(A / B), s)

def isnan(h): return (int(h[:4], 16) & 0x7fff) == 0x7fff and int(h[4:], 16) & ((1 << 63) - 1)

bad = n = 0
for line in sys.stdin:
    f = line.split(); a, b = dec(f[1]), dec(f[2]); n += 1
    for o, got in zip("+-*/", f[3:7]):
        want = op(o, a, b)
        if want == "nan":
            ok = bool(isnan(got))
        else:
            w = enc(*want); ok = (w == got)
        if not ok:
            bad += 1
            if bad <= 15: print("case %s %s %s %s: want %s got %s" % (f[0], f[1], o, f[2], "nan" if want == "nan" else w, got))
    le, ge, eq = int(f[7]), int(f[8]), int(f[9])
    if "nan" not in (a[0], b[0]):
        A, B = val(a), val(b)
        A = F(10) ** 6000 * (1 - 2 * a[1]) if a[0] == "inf" else A
        B = F(10) ** 6000 * (1 - 2 * b[1]) if b[0] == "inf" else B
        if (le <= 0) != (A <= B) or (ge >= 0) != (A >= B) or (eq == 0) != (A == B):
            bad += 1; print("cmp", f[0], f[1], f[2], le, ge, eq)
print("%d cases, %d wrong" % (n, bad))
sys.exit(bad != 0)
