# SPDX-License-Identifier: MIT
"""Reference 3A control algorithms (run on the A53, not in hardware).

They consume the hardware statistics of hwmodel.stats and produce register
values for the next frame. Floating point is fine here; only the statistics
and the ISP data path have to be bit-exact.
"""
from dataclasses import dataclass

import numpy as np

from .stats import NZ


# ---------------------------------------------------------------- AWB
def awb_gray_world(st, min_quads=16, gain_min=256, gain_max=4096):
    """Gray world over zones with enough valid quads. Returns Q10 gains
    (r, gr, gb, b), green kept at 1.0."""
    ok = st["awb_n"] >= min_quads
    r = st["awb_r"][ok].sum()
    g = st["awb_g"][ok].sum() / 2.0          # awb_g holds Gr+Gb
    b = st["awb_b"][ok].sum()
    if r <= 0 or b <= 0 or g <= 0:
        return None
    rg = int(np.clip(round(1024 * g / r), gain_min, gain_max))
    bg = int(np.clip(round(1024 * g / b), gain_min, gain_max))
    return [rg, 1024, 1024, bg]


# ---------------------------------------------------------------- AE
def _center_weights():
    yy, xx = np.mgrid[0:NZ, 0:NZ]
    d = np.hypot(yy - (NZ - 1) / 2, xx - (NZ - 1) / 2) / (NZ / 2)
    return 1.0 + 3.0 * np.clip(1.0 - d, 0, 1)      # centre 4x, corners 1x


@dataclass
class AeState:
    exposure: float = 1.0        # relative exposure (sensor time x analog gain)
    target: float = 0.18 * 1023  # mean linear quad luma target (18 % grey)
    damping: float = 0.5         # 0 = no change, 1 = jump to the estimate
    ratio_max: float = 4.0       # max change per frame
    exp_min: float = 1.0 / 64
    exp_max: float = 64.0


def ae_mean(st, grid):
    """Centre-weighted mean quad luma (10-bit scale)."""
    zw, zh = grid[2], grid[3]
    mean = st["ae_sum"] / ((zw // 2) * (zh // 2))
    w = _center_weights()
    return float((mean * w).sum() / w.sum())


def ae_update(state: AeState, st, grid):
    """One AE step: returns the new relative exposure. Highlight protection:
    if more than 2 % of quads are clipped, the target is lowered."""
    m = max(ae_mean(st, grid), 1.0)
    target = state.target
    hist = st["ae_hist"]
    if hist[250:].sum() > 0.02 * hist.sum():
        target *= 0.7
    ratio = np.clip((target / m) ** state.damping, 1 / state.ratio_max, state.ratio_max)
    state.exposure = float(np.clip(state.exposure * ratio, state.exp_min, state.exp_max))
    return state.exposure


# ---------------------------------------------------------------- AF
def af_score(st, roi=None):
    """Sharpness score for contrast AF (higher is sharper). roi = (zy0, zy1, zx0, zx1)."""
    a = st["af"]
    if roi:
        a = a[roi[0]:roi[1], roi[2]:roi[3]]
    return int(a.sum())


# ================================================================ firmware reference
# The functions below are the reference for the C firmware (sw/lib/oisp_aaa.c);
# tests/test_fw.py checks that both give the same results.  Parameters come
# from the "ae" / "awb" sections of a tuning file (isptune/tuning.py).
import math  # noqa: E402

AE_DEFAULTS = dict(enable=True, target=184.0, damping=0.5, ratio_max=4.0,
                   hl_bin=250, hl_frac=0.02, hl_scale=0.7,
                   lines_min=8, lines_max=2000, flicker_lines=0,
                   gain_1x=16, gain_code_min=16, gain_code_max=128, gain_table=[],
                   dgain_max=4.0, delay=2)
AWB_DEFAULTS = dict(enable=True, min_quads=16, speed=0.5, locus_dist=0.25,
                    gain_min=256, gain_max=8191, illuminants=[])


def ae_measure(st, grid):
    """Centre-weighted mean quad luma, plain loops (same order as the C code)."""
    qn = (grid[2] // 2) * (grid[3] // 2)
    c = (NZ - 1) / 2.0
    num = den = 0.0
    for zy in range(NZ):
        for zx in range(NZ):
            d = math.sqrt((zy - c) ** 2 + (zx - c) ** 2) / (NZ / 2.0)
            w = 1.0 + 3.0 * min(max(1.0 - d, 0.0), 1.0)
            num += w * (int(st["ae_sum"].flat[zy * NZ + zx]) / qn)
            den += w
    return num / den


def ae_target_exposure(st, grid, p, e_used, dg_used):
    """New total exposure (lines x analog gain x digital gain) from the
    statistics of a frame taken with e_used (dg_used of it digital)."""
    m = max(ae_measure(st, grid) * dg_used, 1.0)
    hist = st["ae_hist"]
    tot = sum(int(v) for v in hist)
    hi = sum(int(v) for v in hist[p["hl_bin"]:])
    target = p["target"] * (p["hl_scale"] if hi > p["hl_frac"] * tot else 1.0)
    ratio = (target / m) ** p["damping"]
    ratio = min(max(ratio, 1.0 / p["ratio_max"]), p["ratio_max"])
    return e_used * ratio


def ae_split(e, p):
    """Total exposure -> (lines, analog gain code, analog gain, digital gain).
    Integration time first (multiples of flicker_lines when enabled), then
    analog gain, then digital gain (applied through the WB gains)."""
    lmax = p["lines_max"]
    lines = min(max(e, p["lines_min"]), lmax)
    fl = p["flicker_lines"]
    if fl and lines >= fl:
        lines = math.floor(lines / fl) * fl
    lines = int(math.floor(lines + 0.5))
    g = e / lines
    tab = p.get("gain_table") or []
    if tab:                           # gain control is an index: largest table gain <= g
        lo = min(max(p["gain_code_min"], 0), len(tab) - 1)
        hi = min(max(p["gain_code_max"], lo), len(tab) - 1)
        code = lo
        while code < hi and tab[code + 1] <= g * (1 + 1e-9):
            code += 1
        again = tab[code]
    else:
        code = int(math.floor(g * p["gain_1x"] + 0.5))
        code = min(max(code, p["gain_code_min"]), p["gain_code_max"])
        again = code / p["gain_1x"]
    dgain = min(max(g / again, 1.0), p["dgain_max"])
    return lines, code, again, dgain


def awb_measure(st, min_quads):
    """Gray-world white point -> (log2 r gain, log2 b gain) or None."""
    r = g = b = 0
    for z in range(NZ * NZ):
        if int(st["awb_n"].flat[z]) >= min_quads:
            r += int(st["awb_r"].flat[z])
            g += int(st["awb_g"].flat[z])
            b += int(st["awb_b"].flat[z])
    if r <= 0 or g <= 0 or b <= 0:
        return None
    return math.log2(g / 2.0 / r), math.log2(g / 2.0 / b)


def awb_locus(pt, ills, max_dist):
    """Project pt (log2 gains) on the illuminant polyline (sorted by CCT).
    Returns (point moved to within max_dist of the polyline, segment, fraction)."""
    q = [(math.log2(i["gains"][0] / 1024.0), math.log2(i["gains"][1] / 1024.0)) for i in ills]
    if len(q) == 1:
        return pt, 0, 0.0
    best = None
    for k in range(len(q) - 1):
        ax, ay = q[k]
        dx, dy = q[k + 1][0] - ax, q[k + 1][1] - ay
        L = dx * dx + dy * dy
        f = 0.0 if L == 0 else ((pt[0] - ax) * dx + (pt[1] - ay) * dy) / L
        f = min(max(f, 0.0), 1.0)
        px, py = ax + f * dx, ay + f * dy
        d = math.sqrt((pt[0] - px) ** 2 + (pt[1] - py) ** 2)
        if best is None or d < best[0]:
            best = (d, k, f, px, py)
    d, k, f, px, py = best
    if d > max_dist:
        s = max_dist / d
        pt = (px + (pt[0] - px) * s, py + (pt[1] - py) * s)
    return pt, k, f


def ccm_interp(ills, k, f):
    """Linear interpolation of the Q10 CCMs of illuminants k and k+1; each
    row is rounded and its diagonal adjusted to keep the row sum."""
    a = ills[k]["ccm"]
    b_ = ills[min(k + 1, len(ills) - 1)]["ccm"]
    out = []
    for r in range(3):
        row = [int(math.floor(a[r][c] + f * (b_[r][c] - a[r][c]) + 0.5)) for c in range(4)]
        want = int(math.floor(sum(a[r][:3]) + f * (sum(b_[r][:3]) - sum(a[r][:3])) + 0.5))
        row[r] += want - sum(row[:3])
        out.append(row)
    return out


def awb_cct(ills, k, f):
    if len(ills) == 1:
        return float(ills[0]["cct"])
    m = (1 - f) * 1e6 / ills[k]["cct"] + f * 1e6 / ills[k + 1]["cct"]
    return 1e6 / m


@dataclass
class AwbFwState:
    valid: bool = False
    lr: float = 0.0          # log2 gains (smoothed)
    lb: float = 0.0
    k: int = 0
    f: float = 0.0


def awb_step(state: AwbFwState, st, p):
    """One AWB iteration -> (gain_r, gain_b) Q10 and the CCM (None without
    illuminants) and the CCT estimate."""
    m = awb_measure(st, p["min_quads"])
    ills = p["illuminants"]
    if m is not None:
        if ills:
            m, state.k, state.f = awb_locus(m, ills, p["locus_dist"])
        if not state.valid:
            state.lr, state.lb, state.valid = m[0], m[1], True
        else:
            state.lr += p["speed"] * (m[0] - state.lr)
            state.lb += p["speed"] * (m[1] - state.lb)
    if not state.valid:
        return None
    if ills:  # CCM / CCT follow the smoothed white point
        _, state.k, state.f = awb_locus((state.lr, state.lb), ills, 1e9)
    gr = min(max(int(math.floor(1024.0 * 2.0 ** state.lr + 0.5)), p["gain_min"]), p["gain_max"])
    gb = min(max(int(math.floor(1024.0 * 2.0 ** state.lb + 0.5)), p["gain_min"]), p["gain_max"])
    ccm = ccm_interp(ills, state.k, state.f) if ills else None
    cct = awb_cct(ills, state.k, state.f) if ills else 0.0
    return gr, gb, ccm, cct


def wb_registers(gain_r, gain_b, dgain):
    """AWB gains (Q10) x digital gain -> the four 14-bit awb_gain registers."""
    def q(x):
        return min(max(int(math.floor(x * dgain + 0.5)), 0), 16383)
    return [q(gain_r), q(1024), q(1024), q(gain_b)]
