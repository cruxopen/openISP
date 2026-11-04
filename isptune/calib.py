# SPDX-License-Identifier: MIT
"""Sensor calibration from RAW captures: black level (dark frame), noise
model and denoise starting points (neutral chart patches or a flat field)."""
import numpy as np

from hwmodel.common import B, GB, GR, R, cfa_map

CHANNELS = ("R", "Gr", "Gb", "B")


def load_raw(path, width, height, frame=0):
    """Little-endian uint16 per pixel (V4L2 SRGGB10 layout), 10 valid bits."""
    n = width * height
    a = np.fromfile(path, dtype="<u2", count=n, offset=frame * n * 2)
    if a.size != n:
        raise ValueError(f"{path}: expected {n} pixels, got {a.size}")
    return (a & 0x3FF).reshape(height, width).astype(np.int64)


def black_level(dark, pattern):
    """Per-channel black level (R, Gr, Gb, B) from a dark frame (lens capped).
    Hot pixels (> median + 10 MAD) are excluded and counted."""
    ch = cfa_map(pattern, *dark.shape)
    bl, sigma, hot = [], [], 0
    for c in (R, GR, GB, B):
        v = dark[ch == c].astype(float)
        med = np.median(v)
        mad = max(np.median(np.abs(v - med)), 0.5)
        ok = v <= med + 10 * 1.4826 * mad
        hot += int((~ok).sum())
        bl.append(int(np.floor(v[ok].mean() + 0.5)))
        sigma.append(float(v[ok].std()))
    return {"bl": bl, "read_noise": sigma, "hot_pixels": hot,
            "hot_ppm": 1e6 * hot / dark.size,
            "dpc_thres": int(min(1023, max(16, round(8 * max(sigma)))))}


def noise_model(levels, variances):
    """Fit var = a * level + b (shot noise gain a, read noise variance b)."""
    A = np.stack([np.asarray(levels, float), np.ones(len(levels))], 1)
    (a, b_), *_ = np.linalg.lstsq(A, np.asarray(variances, float), rcond=None)
    return float(a), float(max(b_, 0.0))


def denoise_suggestion(sigma_y):
    """NLM / BNF starting points from the noise of Y (8-bit, after gamma).
    NLM weight exp(-mad^2/h^2) with mad ~ 1.13 sigma for pure noise:
    h = 1.5 sigma keeps noise-level differences at weight ~0.5."""
    s = float(sigma_y)
    h = int(min(60, max(2, round(1.5 * s))))
    t = sorted({int(min(255, max(1, round(k * s)))) for k in (8, 3, 1)}, reverse=True)
    while len(t) < 3:
        t.append(max(1, t[-1] - 1))
    return {"nlm_h": h, "bnf_rthres": t[:3]}


# Starting CCM when no chart has been shot yet (typical CMOS camera -> linear
# sRGB, Q10, rows sum to 1024 so white stays white). Replace with
# `isptune chart` results when a ColorChecker is available.
GENERIC_CCM = [[1638, -461, -153, 0], [-307, 1536, -205, 0], [0, -563, 1587, 0]]


def centre_roi(shape, frac):
    """Central window covering frac of each dimension, even-aligned."""
    h, w = shape
    hh, ww = int(h * frac) // 2 * 2, int(w * frac) // 2 * 2
    y0, x0 = (h - hh) // 4 * 2, (w - ww) // 4 * 2
    return slice(y0, y0 + hh), slice(x0, x0 + ww)


def channel_means(raw, pattern, black, roi, sat=1000):
    """Mean of each Bayer channel (R, Gr, Gb, B) minus black in roi, and the
    fraction of clipped pixels."""
    ch = cfa_map(pattern, *raw.shape)[roi]
    p = raw[roi]
    m = [float(p[ch == c].mean()) - black[c] for c in (R, GR, GB, B)]
    return m, float((p >= sat).mean())


def flat_wb(raw, pattern, black, frac=0.3, sat=1000):
    """White balance gains (Q10, G = 1024) from a white / grey target filling
    the centre of the frame. Returns (gain_r, gain_b), channel means, clip."""
    m, clip = channel_means(raw, pattern, black, centre_roi(raw.shape, frac), sat)
    if min(m) <= 4:
        raise ValueError(f"target too dark (channel means {[round(v, 1) for v in m]})")
    if clip > 0.001:
        raise ValueError(f"{100 * clip:.2f} % of the target is clipped: lower the exposure")
    g = 0.5 * (m[1] + m[2])
    return (int(round(1024 * g / m[0])), int(round(1024 * g / m[3]))), m, clip


def flat_noise(pairs, pattern, black, frac=0.3, sat=1000):
    """Photon transfer from flat-field frame pairs (same exposure): per channel
    and pair, level = mean - black, variance = var(f1 - f2) / 2 (removes
    shading and target texture). Returns (levels, variances), clipped pairs
    skipped."""
    roi = centre_roi(pairs[0][0].shape, frac)
    ch = cfa_map(pattern, *pairs[0][0].shape)[roi]
    lv, var = [], []
    for f1, f2 in pairs:
        a, b_ = f1[roi].astype(float), f2[roi].astype(float)
        if (a >= sat).mean() > 0.001:
            continue
        for c in (R, GR, GB, B):
            k = ch == c
            lv.append(0.5 * (a[k].mean() + b_[k].mean()) - black[c])
            var.append(0.5 * (a[k] - b_[k]).var())
    return lv, var
