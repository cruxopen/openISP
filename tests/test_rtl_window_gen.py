# SPDX-License-Identifier: MIT
"""window_gen RTL vs the golden border rule (numpy 'reflect', as hwmodel.common.pad).

Every output beat (all KH x NPOS pixels, TUSER, TLAST, row parity) is compared
bit for bit. Frames cover the minimum legal sizes, odd/even heights, the
maximum width, runtime size changes, junk before SOF and random
valid/ready back-pressure."""
import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "hw", "sim"))
import rtlsim  # noqa: E402

pytestmark = pytest.mark.skipif(not rtlsim.available(), reason="no RTL simulator (verilator or xsim)")

DW, PPC, MAXW = 10, 2, 3840
CONFIGS = [(5, 5), (9, 9), (7, 7), (3, 5)]          # DPC/AAF/CFA/BNF, CNF, NLM, EE


def frame_sizes(kh, kw, rng):
    r, c = kh // 2, kw // 2
    wmin = max(3 * PPC, ((c + 1 + PPC - 1) // PPC) * PPC)
    sizes = [(wmin, r + 1), (wmin, r + 2), (wmin + PPC, kh), (16, kh + 1),
             (MAXW, r + 2)]
    for _ in range(6):
        sizes.append((int(rng.integers(wmin // PPC, 40)) * PPC, int(rng.integers(r + 1, 24))))
    return sizes


def expected(img, kh, kw):
    r, c = kh // 2, kw // 2
    npos = PPC + 2 * c
    p = np.pad(img, ((r, r), (c, c)), mode="reflect")
    h, w = img.shape
    beats = []
    for y in range(h):
        for o in range(w // PPC):
            patch = p[y:y + kh, o * PPC:o * PPC + npos]
            v = 0
            for k in range(kh):
                for i in range(npos):
                    v |= int(patch[k, i]) << ((k * npos + i) * DW)
            beats.append((int(y == 0 and o == 0), int(o == w // PPC - 1), y & 1, v))
    return beats


def write_stim(path, frames, rng):
    with open(path, "w") as f:
        for n, img in enumerate(frames):
            for _ in range(int(rng.integers(0, 3)) if n else 2):     # junk before SOF
                f.write(f"J {int(rng.integers(0, 1 << (PPC * DW))):x}\n")
            h, w = img.shape
            f.write(f"F {w} {h}\n")
            for y in range(h):
                for o in range(w // PPC):
                    v = 0
                    for pp in range(PPC):
                        v |= int(img[y, o * PPC + pp]) << (pp * DW)
                    f.write(f"{v:x}\n")


def read_out(path):
    out = []
    with open(path) as f:
        for ln in f:
            u, l, yo, d = ln.split()
            out.append((int(u), int(l), int(yo), int(d, 16)))
    return out


@pytest.fixture(scope="module", params=CONFIGS, ids=lambda k: f"{k[0]}x{k[1]}")
def sim(request, tmp_path_factory):
    kh, kw = request.param
    wd = tmp_path_factory.mktemp(f"wg_{kh}x{kw}")
    s = rtlsim.Sim(str(wd), "tb_window_gen",
                 ["rtl/common/window_gen.sv", "rtl/common/axis_skid.sv", "sim/tb_window_gen.sv"],
                 {"KH": kh, "KW": kw, "DW": DW, "PPC": PPC, "MAXW": MAXW})
    return s, kh, kw


@pytest.mark.parametrize("vpct,rpct,seed", [(100, 100, 1), (70, 60, 2), (40, 90, 3)])
def test_window_gen_bit_exact(sim, vpct, rpct, seed):
    s, kh, kw = sim
    rng = np.random.default_rng(seed * 100 + kh * 10 + kw)
    frames = [rng.integers(0, 1 << DW, (h, w)) for (w, h) in frame_sizes(kh, kw, rng)]
    exp = [b for f in frames for b in expected(f, kh, kw)]
    stim = os.path.join(s.dir, f"stim_{seed}.txt")
    out = os.path.join(s.dir, f"out_{seed}.txt")
    write_stim(stim, frames, rng)
    res = s.run(STIM=stim, OUT=out, NOUT=len(exp), SEED=seed, VALID_PCT=vpct, READY_PCT=rpct)
    got = read_out(out)
    assert "DONE" in res, res
    assert len(got) == len(exp)
    for n, (g, e) in enumerate(zip(got, exp)):
        assert g == e, f"beat {n}: got (u,l,yodd)={g[:3]} exp={e[:3]} data_equal={g[3] == e[3]}"


def test_window_gen_full_rate_throughput(sim):
    """At full rate a frame takes (H + R) passes of (W/PPC + D) beats."""
    s, kh, kw = sim
    r, c = kh // 2, kw // 2
    d = (c + PPC - 1) // PPC
    w, h = MAXW, 12
    rng = np.random.default_rng(7)
    frames = [rng.integers(0, 1 << DW, (h, w))]
    exp = expected(frames[0], kh, kw)
    stim = os.path.join(s.dir, "stim_tp.txt")
    out = os.path.join(s.dir, "out_tp.txt")
    with open(stim, "w") as f:                       # no junk: measure the frame only
        f.write(f"F {w} {h}\n")
        for y in range(h):
            for o in range(w // PPC):
                f.write(f"{int(frames[0][y, 2*o]) | int(frames[0][y, 2*o+1]) << DW:x}\n")
    res = s.run(STIM=stim, OUT=out, NOUT=len(exp), SEED=9, VALID_PCT=100, READY_PCT=100)
    cycles = int(res.split()[-2])
    ideal = r * (w // PPC) + h * (w // PPC + d)    # R fill passes + H output passes
    assert read_out(out) == exp
    assert cycles <= ideal + 100, (cycles, ideal)   # +reset/pipeline latency
