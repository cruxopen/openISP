# SPDX-License-Identifier: MIT
"""Colour calibration from a RAW capture of a 24-patch ColorChecker:
patch extraction, white balance gains, CCM fit, colour error (CIEDE2000).

Working space: the CCM block sees linear 10-bit RGB after BLC and the WB
gains; its target is linear sRGB (the gamma LUT then applies the transfer
curve), so the fit maps white-balanced camera RGB to linear sRGB.
"""
import numpy as np

from hwmodel.common import B, GB, GR, R, cfa_map

# sRGB (D65) values of the ColorChecker Classic, row by row (patch 1..24)
CHECKER_SRGB = np.array([
    [115, 82, 68], [194, 150, 130], [98, 122, 157], [87, 108, 67], [133, 128, 177], [103, 189, 170],
    [214, 126, 44], [80, 91, 166], [193, 90, 99], [94, 60, 108], [157, 188, 64], [224, 163, 46],
    [56, 61, 150], [70, 148, 73], [175, 54, 60], [231, 199, 31], [187, 86, 149], [8, 133, 161],
    [243, 243, 242], [200, 200, 200], [160, 160, 160], [122, 122, 121], [85, 85, 85], [52, 52, 52],
], float)
NEUTRALS = list(range(18, 24))            # white .. black
ILLUMINANT_CCT = {"A": 2856, "TL84": 4000, "CWF": 4150, "D50": 5003, "D55": 5503, "D65": 6504,
                  "D75": 7504, "HZ": 2300, "U30": 3000, "F11": 4000}


def srgb_to_linear(v8):
    c = np.asarray(v8, float) / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb8(lin):
    c = np.clip(lin, 0, 1)
    return 255.0 * np.where(c <= 0.0031308, 12.92 * c, 1.055 * c ** (1 / 2.4) - 0.055)


_M_RGB2XYZ = np.array([[0.4124564, 0.3575761, 0.1804375],
                       [0.2126729, 0.7151522, 0.0721750],
                       [0.0193339, 0.1191920, 0.9503041]])
_WHITE = np.array([0.95047, 1.0, 1.08883])


def linear_to_lab(lin):
    xyz = np.asarray(lin, float) @ _M_RGB2XYZ.T / _WHITE
    f = np.where(xyz > (6 / 29) ** 3, np.cbrt(xyz), xyz / (3 * (6 / 29) ** 2) + 4 / 29)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1)


def delta_e2000(lab1, lab2):
    """CIEDE2000 colour difference (Sharma et al. 2005)."""
    L1, a1, b1 = np.moveaxis(np.asarray(lab1, float), -1, 0)
    L2, a2, b2 = np.moveaxis(np.asarray(lab2, float), -1, 0)
    C1, C2 = np.hypot(a1, b1), np.hypot(a2, b2)
    Cm = (C1 + C2) / 2
    G = 0.5 * (1 - np.sqrt(Cm ** 7 / (Cm ** 7 + 25.0 ** 7)))
    a1p, a2p = (1 + G) * a1, (1 + G) * a2
    C1p, C2p = np.hypot(a1p, b1), np.hypot(a2p, b2)
    h1p = np.degrees(np.arctan2(b1, a1p)) % 360
    h2p = np.degrees(np.arctan2(b2, a2p)) % 360
    dLp, dCp = L2 - L1, C2p - C1p
    dh = h2p - h1p
    dh = np.where(C1p * C2p == 0, 0, np.where(dh > 180, dh - 360, np.where(dh < -180, dh + 360, dh)))
    dHp = 2 * np.sqrt(C1p * C2p) * np.sin(np.radians(dh / 2))
    Lpm, Cpm = (L1 + L2) / 2, (C1p + C2p) / 2
    hs = h1p + h2p
    hpm = np.where(C1p * C2p == 0, hs,
                   np.where(np.abs(h1p - h2p) <= 180, hs / 2, np.where(hs < 360, (hs + 360) / 2, (hs - 360) / 2)))
    T = (1 - 0.17 * np.cos(np.radians(hpm - 30)) + 0.24 * np.cos(np.radians(2 * hpm))
         + 0.32 * np.cos(np.radians(3 * hpm + 6)) - 0.20 * np.cos(np.radians(4 * hpm - 63)))
    dth = 30 * np.exp(-(((hpm - 275) / 25) ** 2))
    Rc = 2 * np.sqrt(Cpm ** 7 / (Cpm ** 7 + 25.0 ** 7))
    Sl = 1 + 0.015 * (Lpm - 50) ** 2 / np.sqrt(20 + (Lpm - 50) ** 2)
    Sc, Sh = 1 + 0.045 * Cpm, 1 + 0.015 * Cpm * T
    Rt = -np.sin(np.radians(2 * dth)) * Rc
    return np.sqrt((dLp / Sl) ** 2 + (dCp / Sc) ** 2 + (dHp / Sh) ** 2 + Rt * (dCp / Sc) * (dHp / Sh))


# ------------------------------------------------------------------ patches
def patch_centres(corners, rows=4, cols=6):
    """corners: (x, y) of the outer corners of patches 1, 6, 24, 19 (TL, TR,
    BR, BL of the patch grid).  Returns centres (24, 2) and the patch pitch."""
    tl, tr, br, bl = (np.asarray(c, float) for c in corners)
    out = []
    for r in range(rows):
        for c in range(cols):
            u, v = (c + 0.5) / cols, (r + 0.5) / rows
            top = tl + u * (tr - tl)
            bot = bl + u * (br - bl)
            out.append(top + v * (bot - top))
    pitch = min(np.linalg.norm(tr - tl) / cols, np.linalg.norm(bl - tl) / rows)
    return np.array(out), pitch


def patch_means(raw, pattern, corners, black, frac=0.4):
    """Mean camera (R, G, B) per patch, black level subtracted, from the
    central frac x pitch square of each patch (Bayer channels averaged
    separately, G = (Gr + Gb) / 2).  Also returns the max raw value per patch."""
    cen, pitch = patch_centres(corners)
    half = max(int(pitch * frac / 2), 2)
    ch = cfa_map(pattern, *raw.shape)
    bl = np.zeros(raw.shape)
    for k, c in enumerate((R, GR, GB, B)):
        bl[ch == c] = black[k]
    x = raw.astype(float) - bl
    rgb, peak = [], []
    for cx, cy in cen:
        y0, x0 = int(round(cy)) - half, int(round(cx)) - half
        sl = (slice(y0, y0 + 2 * half), slice(x0, x0 + 2 * half))
        p, c = x[sl], ch[sl]
        g = (p[c == GR].mean() + p[c == GB].mean()) / 2
        rgb.append([p[c == R].mean(), g, p[c == B].mean()])
        peak.append(raw[sl].max())
    return np.array(rgb), np.array(peak)


# ------------------------------------------------------------------ fit
def wb_gains(rgb, peak, sat=1000):
    """Q10 gains (r, b) from the unclipped neutral patches (green = 1.0)."""
    use = [i for i in NEUTRALS if peak[i] < sat and rgb[i].min() > 4]
    if not use:
        raise ValueError("no usable neutral patch (all clipped or too dark)")
    s = rgb[use].sum(0)
    return 1024.0 * s[1] / s[0], 1024.0 * s[1] / s[2]


def fit_ccm(cam, target, refine=True):
    """3x3 matrix M with rows summing to 1 (white stays white) minimising
    |M cam - target|^2 in linear RGB, then (refine) the mean CIEDE2000."""
    m = np.zeros((3, 3))
    for r in range(3):
        A = np.stack([cam[:, 0] - cam[:, 2], cam[:, 1] - cam[:, 2]], 1)
        y = target[:, r] - cam[:, 2]
        (a, b_), *_ = np.linalg.lstsq(A, y, rcond=None)
        m[r] = [a, b_, 1 - a - b_]
    if refine:
        try:
            from scipy.optimize import least_squares
        except ImportError:
            return m
        lab_t = linear_to_lab(target)

        def unpack(p):
            q = p.reshape(3, 2)
            return np.column_stack([q, 1 - q.sum(1)])

        def res(p):
            return delta_e2000(linear_to_lab(np.clip(cam @ unpack(p).T, 0, None)), lab_t)

        m = unpack(least_squares(res, m[:, :2].ravel(), method="lm").x)
    return m


def quantize_ccm(m):
    """Float 3x3 -> Q10 3x4 (offset 0); rows keep summing to exactly 1024."""
    q = np.floor(np.asarray(m) * 1024 + 0.5).astype(int)
    for r in range(3):
        q[r, r] += 1024 - q[r].sum()
    return [[int(v) for v in row] + [0] for row in q]


def calibrate(raw, pattern, corners, black, sat=1000):
    """Full chart calibration -> dict(gains, ccm (Q10 3x4), report)."""
    rgb, peak = patch_means(raw, pattern, corners, black)
    gr, gb = wb_gains(rgb, peak, sat)
    cam = rgb * np.array([gr / 1024, 1.0, gb / 1024])
    tgt = srgb_to_linear(CHECKER_SRGB)
    ok = np.array([peak[i] < sat for i in range(24)])
    # exposure: scale the camera so the neutral patches match in G
    neu = [i for i in NEUTRALS if ok[i]]
    s = (tgt[neu, 1] * cam[neu, 1]).sum() / (cam[neu, 1] ** 2).sum()
    cam = cam * s
    m = fit_ccm(cam[ok], tgt[ok])
    q = quantize_ccm(m)
    mq = np.asarray(q)[:, :3] / 1024.0
    lab_t = linear_to_lab(tgt)
    de_before = delta_e2000(linear_to_lab(np.clip(cam, 0, None)), lab_t)
    de_after = delta_e2000(linear_to_lab(np.clip(cam @ mq.T, 0, None)), lab_t)
    return {"gains": [int(round(gr)), int(round(gb))], "ccm": q,
            "report": {"patches_used": int(ok.sum()),
                       "de2000_wb_only": {"mean": float(de_before[ok].mean()), "max": float(de_before[ok].max())},
                       "de2000_ccm": {"mean": float(de_after[ok].mean()), "max": float(de_after[ok].max())}}}
