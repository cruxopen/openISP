# SPDX-License-Identifier: MIT
"""3A statistics - bit-accurate definition of what the hardware collects.

Zone grid: NZ x NZ zones (NZ = 16). Zone (zy, zx) covers pixels
    x in [x0 + zx*zw, x0 + (zx+1)*zw),  y in [y0 + zy*zh, y0 + (zy+1)*zh)
x0, y0, zw, zh are registers and must be even (zones contain whole 2x2
Bayer quads). Pixels outside the NZ*zw x NZ*zh window are not counted.

AWB / AE  (tap: Bayer after DPC + BLC, before the white balance gains)
    per 2x2 quad: R, Gr, Gb, B (10 bit)
    awb_valid = max(R,Gr,Gb,B) < awb_sat  and  min(R,Gr,Gb,B) >= awb_dark
    awb_r[z]  = sum R        over valid quads      (32 bit)
    awb_g[z]  = sum (Gr+Gb)  over valid quads      (32 bit)
    awb_b[z]  = sum B        over valid quads      (32 bit)
    awb_n[z]  = number of valid quads              (32 bit)
    L         = (R + Gr + Gb + B + 2) >> 2         (10 bit quad luma)
    ae_sum[z] = sum L over all quads of the zone   (32 bit)
    ae_hist[b]= number of quads in the window with L >> 2 == b, 256 bins

AF        (tap: Y after CSC, 8 bit; mirror borders as everywhere else)
    g = |Y(x+1,y) - Y(x-1,y)| + |Y(x,y+1) - Y(x,y-1)|
    af[z] = sum over the zone of (g if g > af_coring else 0)   (32 bit)
"""
import numpy as np

from .common import B, GB, GR, I, R, Win, cfa_map

NZ = 16


def zone_grid(width, height, x0=0, y0=0, zw=0, zh=0):
    """zw = zh = 0 selects the automatic grid: largest even zone size that
    fits and, if x0 = y0 = 0, a centred window. Explicit sizes are used as is."""
    auto = not zw and not zh
    if not zw:
        zw = ((width - x0) // NZ) & ~1
    if not zh:
        zh = ((height - y0) // NZ) & ~1
    if auto and not x0 and not y0:
        x0 = ((width - NZ * zw) // 2) & ~1
        y0 = ((height - NZ * zh) // 2) & ~1
    assert x0 % 2 == 0 and y0 % 2 == 0 and zw % 2 == 0 and zh % 2 == 0 and zw and zh
    assert x0 + NZ * zw <= width and y0 + NZ * zh <= height
    return x0, y0, zw, zh


def _zone_sum(a, zw, zh):
    """Sum a (NZ*zh, NZ*zw)-shaped array per zone -> (NZ, NZ)."""
    return a.reshape(NZ, zh, NZ, zw).sum(axis=(1, 3))


def bayer_stats(raw, pattern, grid, awb_sat, awb_dark):
    x0, y0, zw, zh = grid
    win = raw[y0:y0 + NZ * zh, x0:x0 + NZ * zw].astype(I)
    ch = cfa_map(pattern, *win.shape)
    q = {c: win[ch == c].reshape(win.shape[0] // 2, win.shape[1] // 2) for c in (R, GR, GB, B)}
    r, gr, gb, b = q[R], q[GR], q[GB], q[B]
    stack = np.stack([r, gr, gb, b])
    valid = (stack.max(0) < awb_sat) & (stack.min(0) >= awb_dark)
    qw, qh = zw // 2, zh // 2
    lum = (r + gr + gb + b + 2) >> 2
    return {
        "awb_r": _zone_sum(np.where(valid, r, 0), qw, qh),
        "awb_g": _zone_sum(np.where(valid, gr + gb, 0), qw, qh),
        "awb_b": _zone_sum(np.where(valid, b, 0), qw, qh),
        "awb_n": _zone_sum(valid.astype(I), qw, qh),
        "ae_sum": _zone_sum(lum, qw, qh),
        "ae_hist": np.bincount((lum >> 2).ravel(), minlength=256).astype(I),
    }


def af_stats(y, grid, coring):
    x0, y0, zw, zh = grid
    w = Win(y, 1)
    g = np.abs(w(0, 1) - w(0, -1)) + np.abs(w(1, 0) - w(-1, 0))
    g = np.where(g > coring, g, 0)
    return {"af": _zone_sum(g[y0:y0 + NZ * zh, x0:x0 + NZ * zw], zw, zh)}
