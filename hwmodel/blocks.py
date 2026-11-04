# SPDX-License-Identifier: MIT
"""Bit-accurate integer blocks of the openISP hardware pipeline.

Each function is the reference ("golden") behaviour of one hardware block.
Data formats:
  Bayer domain : uint10 in int64 arrays, shape (H, W)
  RGB domain   : uint10 (after CFA/CCM) or uint8 (after gamma), shape (H, W, 3)
  YUV domain   : uint8, BT.601 limited range, Y (H, W) and UV (H, W, 2)

Deviations from the floating-point openISP reference are listed in
docs/hw/golden_model.md; every one of them fixes a bug or makes the
arithmetic implementable in hardware.
"""
import numpy as np

from .common import I, B, GB, GR, R, Win, cfa_map, clip, div_round, pad, rshr

BAYER_MAX = 1023


# --------------------------------------------------------------------------- DPC
def dpc(raw, thres, mode="gradient"):
    """Dead pixel correction on same-colour neighbours at distance 2.

        p1 . p2 . p3
        p4 . p0 . p5      p0 is corrected when it differs from all eight
        p6 . p7 . p8      neighbours by more than `thres`.
    """
    w = Win(raw, 2)
    p0 = w(0, 0)
    p1, p2, p3 = w(-2, -2), w(-2, 0), w(-2, 2)
    p4, p5 = w(0, -2), w(0, 2)
    p6, p7, p8 = w(2, -2), w(2, 0), w(2, 2)
    dead = np.ones(p0.shape, bool)
    for p in (p1, p2, p3, p4, p5, p6, p7, p8):
        dead &= np.abs(p - p0) > thres
    if mode == "mean":
        fix = rshr(p2 + p4 + p5 + p7, 2)
    elif mode == "gradient":
        grads = np.stack([np.abs(2 * p0 - p2 - p7),   # vertical
                          np.abs(2 * p0 - p4 - p5),   # horizontal
                          np.abs(2 * p0 - p1 - p8),   # diagonal \
                          np.abs(2 * p0 - p3 - p6)])  # diagonal /
        cand = np.stack([p2 + p7, p4 + p5, p1 + p8, p3 + p6])
        sel = np.argmin(grads, axis=0)                 # first minimum wins
        fix = rshr(np.take_along_axis(cand, sel[None], 0)[0], 1)
    else:
        raise ValueError(mode)
    return clip(np.where(dead, fix, p0), 0, BAYER_MAX)


# --------------------------------------------------------------------------- BLC
def blc(raw, pattern, bl, alpha=0, beta=0):
    """Black level subtraction. bl = (r, gr, gb, b) in 10-bit codes.
    Optional crosstalk compensation: Gr += alpha*R/256, Gb += beta*B/256
    using the R/B sample of the same 2x2 quad (after black level)."""
    ch = cfa_map(pattern, *raw.shape)
    out = raw.astype(I) - np.asarray(bl, I)[ch]
    out = clip(out, 0, BAYER_MAX)
    if alpha or beta:
        quad = out.reshape(out.shape[0] // 2, 2, out.shape[1] // 2, 2)
        chq = ch.reshape(quad.shape)
        rv = (quad * (chq == R)).sum(axis=(1, 3))
        bv = (quad * (chq == B)).sum(axis=(1, 3))
        rv = np.repeat(np.repeat(rv, 2, 0), 2, 1)
        bv = np.repeat(np.repeat(bv, 2, 0), 2, 1)
        out = out + np.where(ch == GR, rshr(alpha * rv, 8), 0) \
                  + np.where(ch == GB, rshr(beta * bv, 8), 0)
        out = clip(out, 0, BAYER_MAX)
    return out


# --------------------------------------------------------------------------- AAF
def aaf(raw):
    """Anti-aliasing: 3x3 same-colour average, centre weight 8 of 16."""
    w = Win(raw, 2)
    s = 8 * w(0, 0)
    for dy in (-2, 0, 2):
        for dx in (-2, 0, 2):
            if dy or dx:
                s = s + w(dy, dx)
    return rshr(s, 4)


# --------------------------------------------------------------------------- AWB gain
def awb_gain(raw, pattern, gains_q10):
    """White balance gains, Q10 (1024 = 1.0), order (r, gr, gb, b)."""
    ch = cfa_map(pattern, *raw.shape)
    g = np.asarray(gains_q10, I)[ch]
    return clip(rshr(raw.astype(I) * g, 10), 0, BAYER_MAX)


# --------------------------------------------------------------------------- CNF
CNF_RECIP = {25: 2621, 16: 4096, 40: 1638}           # round(65536 / n)
CNF_METER_T = (120, 200, 280, 400, 600, 800, 1000)    # openISP 8-bit thresholds x4
CNF_FADE1 = (256, 230, 205, 179, 154, 77, 26, 0)      # 1.0 .9 .8 .7 .6 .3 .1 0
CNF_C1_T = (120, 200, 280, 400, 600, 800)
CNF_FADE2 = (256, 230, 205, 154, 128, 77, 0)          # 1.0 .9 .8 .6 .5 .3 0


def _step_lut(v, thresholds, values):
    """values[k] where k = number of thresholds strictly below v (v <= t0 -> values[0])."""
    k = np.zeros(v.shape, I)
    for t in thresholds:
        k += v > t
    return np.asarray(values, I)[k]


def _damp_q8(gain_q10):
    if gain_q10 <= 1024:
        return 256
    if gain_q10 <= 1229:
        return 128
    return 77


def cnf(raw, pattern, gains_q10, thres=0):
    """Chroma noise filter on R and B sites, 9x9 window.

    C1 = samples of the centre colour (25), C2 = the other chroma (16),
    G = green samples (40).  A centre that sticks out above its neighbours
    while its own colour plane also sticks out is pulled toward
    max(avgG, avgC2), faded by local brightness and chroma level."""
    w = Win(raw, 4)
    sg = sc1 = sc2 = 0
    for dy in range(-4, 5):
        for dx in range(-4, 5):
            v = w(dy, dx)
            if dy % 2 == 0 and dx % 2 == 0:
                sc1 = sc1 + v
            elif dy % 2 and dx % 2:
                sc2 = sc2 + v
            else:
                sg = sg + v
    avg_g = rshr(sg * CNF_RECIP[40], 16)
    avg_c1 = rshr(sc1 * CNF_RECIP[25], 16)
    avg_c2 = rshr(sc2 * CNF_RECIP[16], 16)
    c = w(0, 0)

    noise = (c > avg_g + thres) & (c > avg_c2 + thres) & \
            (avg_c1 > avg_g + thres) & (avg_c1 > avg_c2 + thres)

    ch = cfa_map(pattern, *raw.shape)
    is_r, is_b = ch == R, ch == B
    damp = np.where(is_r, _damp_q8(gains_q10[0]), _damp_q8(gains_q10[3]))
    m = np.maximum(avg_g, avg_c2)
    corrected = m + rshr(damp * (c - m), 8)
    # luma estimate 0.299 R + 0.587 G + 0.114 B (C1 is R on R sites, B on B sites)
    meter = np.where(is_r, rshr(77 * avg_c1 + 150 * avg_g + 29 * avg_c2, 8),
                           rshr(77 * avg_c2 + 150 * avg_g + 29 * avg_c1, 8))
    fade = rshr(_step_lut(meter, CNF_METER_T, CNF_FADE1) *
                _step_lut(avg_c1, CNF_C1_T, CNF_FADE2), 8)
    out = c + rshr(fade * (corrected - c), 8)
    out = np.where(noise & (is_r | is_b), out, c)
    return clip(out, 0, BAYER_MAX)


# --------------------------------------------------------------------------- CFA
def cfa_malvar(raw, pattern):
    """Malvar-He-Cutler demosaic, all kernels scaled to /16."""
    w = Win(raw, 2)
    c = w(0, 0)
    n1, s1, w1, e1 = w(-1, 0), w(1, 0), w(0, -1), w(0, 1)
    n2, s2, w2, e2 = w(-2, 0), w(2, 0), w(0, -2), w(0, 2)
    d = w(-1, -1) + w(-1, 1) + w(1, -1) + w(1, 1)
    sum1 = n1 + s1 + w1 + e1
    sum2 = n2 + s2 + w2 + e2

    g_at_rb = rshr(8 * c - 2 * sum2 + 4 * sum1, 4)
    opp_at_rb = rshr(12 * c - 3 * sum2 + 4 * d, 4)
    hor_at_g = rshr(10 * c - 2 * (w2 + e2) - 2 * d + (n2 + s2) + 8 * (w1 + e1), 4)
    ver_at_g = rshr(10 * c - 2 * (n2 + s2) - 2 * d + (w2 + e2) + 8 * (n1 + s1), 4)

    ch = cfa_map(pattern, *raw.shape)
    r = np.select([ch == R, ch == GR, ch == GB, ch == B], [c, hor_at_g, ver_at_g, opp_at_rb])
    g = np.select([ch == R, ch == GR, ch == GB, ch == B], [g_at_rb, c, c, g_at_rb])
    b = np.select([ch == R, ch == GR, ch == GB, ch == B], [opp_at_rb, ver_at_g, hor_at_g, c])
    return clip(np.stack([r, g, b], -1), 0, BAYER_MAX)


# --------------------------------------------------------------------------- CCM
def ccm(rgb, m_q10):
    """3x4 colour matrix, Q10 coefficients, offsets in Q10 units; 10-bit in/out."""
    m = np.asarray(m_q10, I)
    out = np.einsum("hwc,kc->hwk", rgb.astype(I), m[:, :3]) + m[:, 3]
    return clip(rshr(out, 10), 0, BAYER_MAX)


# --------------------------------------------------------------------------- Gamma
def gamma_lut(gamma):
    """1024-entry 10-bit -> 8-bit LUT; the table itself is the specification."""
    x = np.arange(1024) / 1023.0
    return np.floor(255.0 * x ** gamma + 0.5).astype(I)


def gamma(rgb10, lut):
    return np.asarray(lut, I)[rgb10]


# --------------------------------------------------------------------------- CSC
def csc(rgb8, m_q10):
    """RGB -> YUV (default BT.601 limited range), Q10 coefficients."""
    m = np.asarray(m_q10, I)
    out = np.einsum("hwc,kc->hwk", rgb8.astype(I), m[:, :3]) + m[:, 3]
    return clip(rshr(out, 10), 0, 255)


# --------------------------------------------------------------------------- NLM
def nlm_lut(h, dshift=3):
    """Weight LUT indexed by (SAD >> dshift), 256 entries, 8-bit weights.
    Emulates exp(-mad^2 / h^2) with mad = SAD / 9 (mean absolute difference)."""
    mad = (np.arange(256) << dshift) / 9.0
    return np.floor(255.0 * np.exp(-(mad * mad) / float(h * h)) + 0.5).astype(I)


def nlm(y, lut, search_r=2, dshift=3):
    """Non-local means on Y: 3x3 patches, (2*search_r+1)^2 search window,
    SAD distance, LUT weights, centre weight = max neighbour weight."""
    h, w_ = y.shape
    rr = search_r + 1
    q = pad(y.astype(I), rr)
    lut = np.asarray(lut, I)

    def sl(dy, dx, m):  # region extended by m pixels on each side
        return q[rr + dy - m:rr + dy + h + m, rr + dx - m:rr + dx + w_ + m]

    base_ext = sl(0, 0, 1)
    num = np.zeros((h, w_), I)
    den = np.zeros((h, w_), I)
    wmax = np.zeros((h, w_), I)
    for dy in range(-search_r, search_r + 1):
        for dx in range(-search_r, search_r + 1):
            if dy == 0 and dx == 0:
                continue
            ad = np.abs(sl(dy, dx, 1) - base_ext)
            sad = sum(ad[i:i + h, j:j + w_] for i in range(3) for j in range(3))
            wt = lut[np.minimum(sad >> dshift, 255)]
            num += wt * sl(dy, dx, 0)
            den += wt
            wmax = np.maximum(wmax, wt)
    c = sl(0, 0, 0)
    num += wmax * c
    den += wmax
    out = np.where(den > 0, div_round(num, np.maximum(den, 1)), c)
    return clip(out, 0, 255)


# --------------------------------------------------------------------------- BNF
def bnf(y, dw, rw, rthres):
    """5x5 bilateral filter: weight = dw[i][j] * rw[level(|p - centre|)].
    rthres is descending (t0 > t1 > t2):
       d >= t0 -> rw[0], d >= t1 -> rw[1], d >= t2 -> rw[2], else rw[3]."""
    win = Win(y, 2)
    c = win(0, 0)
    num = np.zeros(c.shape, I)
    den = np.zeros(c.shape, I)
    t0, t1, t2 = rthres
    for i in range(5):
        for j in range(5):
            p = win(i - 2, j - 2)
            d = np.abs(p - c)
            r = np.select([d >= t0, d >= t1, d >= t2], [rw[0], rw[1], rw[2]], rw[3])
            wt = int(dw[i][j]) * r
            num += wt * p
            den += wt
    out = np.where(den > 0, div_round(num, np.maximum(den, 1)), c)
    return clip(out, 0, 255)


# --------------------------------------------------------------------------- EE
def ee(y, edge_filter, gain, thres, emclip):
    """Edge enhancement with a 3x5 high-pass and a cored gain curve:
         |em| <  t0       : g0 * em
         t0 <= |em| <= t1 : 0
         |em| >  t1       : g1 * em        (gain in Q8, result clipped to emclip)
    Returns (enhanced Y, edge map)."""
    win = Win(y, 2)
    s = 0
    for i in range(3):
        for j in range(5):
            if edge_filter[i][j]:
                s = s + int(edge_filter[i][j]) * win(i - 1, j - 2)
    em = rshr(s, 3)
    a = np.abs(em)
    t0, t1 = thres
    g = np.where(a < t0, gain[0], np.where(a <= t1, 0, gain[1]))
    delta = clip(rshr(g * em, 8), emclip[0], emclip[1])
    return clip(win(0, 0) + delta, 0, 255), em


# --------------------------------------------------------------------------- FCS
def fcs(uv, em, edge, gain):
    """False colour suppression: chroma gain (Q8) gain for |em| <= e0,
    0 for |em| >= e1, linear in between. (e1 - e0) must be a power of two."""
    e0, e1 = edge
    span = e1 - e0
    k = span.bit_length() - 1
    assert span > 0 and (1 << k) == span, "fcs_edge_max - fcs_edge_min must be a power of two"
    a = np.abs(em)
    g = np.where(a <= e0, gain, np.where(a >= e1, 0, (gain * (e1 - a)) >> k))
    c = uv.astype(I) - 128
    return clip(rshr(c * g[..., None], 8) + 128, 0, 255)


# --------------------------------------------------------------------------- HSC
def hsc_tables():
    a = np.arange(360) * np.pi / 180.0
    return (np.floor(256 * np.sin(a) + 0.5).astype(I),
            np.floor(256 * np.cos(a) + 0.5).astype(I))


def hsc(uv, hue_deg, sat_q8):
    """Hue rotation (degrees) then saturation gain (Q8, 256 = 1.0)."""
    sin_t, cos_t = hsc_tables()
    s, c = int(sin_t[hue_deg % 360]), int(cos_t[hue_deg % 360])
    u = uv[..., 0].astype(I) - 128
    v = uv[..., 1].astype(I) - 128
    u1 = rshr(u * c + v * s, 8)
    v1 = rshr(v * c - u * s, 8)
    out = np.stack([rshr(u1 * sat_q8, 8), rshr(v1 * sat_q8, 8)], -1) + 128
    return clip(out, 0, 255)


# --------------------------------------------------------------------------- BCC
def bcc(y, brightness, contrast_q5):
    """Y' = Y + brightness + (Y - 128) * contrast / 32."""
    yy = y.astype(I)
    return clip(yy + brightness + rshr((yy - 128) * contrast_q5, 5), 0, 255)


def yuyv(yuv):
    """Output packing (after the ISP, hw/rtl/top/isp_yuyv.sv): YUV 4:4:4 ->
    4:2:2 YUYV. Per horizontal pixel pair, U = (U0 + U1 + 1) >> 1 rides with
    pixel 0 and V = (V0 + V1 + 1) >> 1 with pixel 1.  Returns (H, W, 2)
    [Y, C]; in memory Y0 U Y1 V (V4L2_PIX_FMT_YUYV)."""
    a = np.asarray(yuv).astype(np.int64)
    out = np.empty(a.shape[:2] + (2,), np.int64)
    out[..., 0] = a[..., 0]
    out[:, 0::2, 1] = (a[:, 0::2, 1] + a[:, 1::2, 1] + 1) >> 1
    out[:, 1::2, 1] = (a[:, 0::2, 2] + a[:, 1::2, 2] + 1) >> 1
    return out.astype(np.uint8)
