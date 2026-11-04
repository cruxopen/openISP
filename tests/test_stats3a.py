# SPDX-License-Identifier: MIT
"""3A statistics (bit-exact spec) and reference control loops."""
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hwmodel import aaa                                   # noqa: E402
from hwmodel import stats as s3a                          # noqa: E402
from hwmodel.config import HwConfig                       # noqa: E402
from hwmodel.pipeline import run                          # noqa: E402


def brute_bayer(raw, grid, sat, dark):
    """Plain loops over quads: an independent reading of the spec (RGGB)."""
    x0, y0, zw, zh = grid
    out = {k: np.zeros((16, 16), np.int64) for k in ("awb_r", "awb_g", "awb_b", "awb_n", "ae_sum")}
    hist = np.zeros(256, np.int64)
    for zy in range(16):
        for zx in range(16):
            for y in range(y0 + zy * zh, y0 + (zy + 1) * zh, 2):
                for x in range(x0 + zx * zw, x0 + (zx + 1) * zw, 2):
                    r, gr, gb, b = (int(raw[y, x]), int(raw[y, x + 1]),
                                    int(raw[y + 1, x]), int(raw[y + 1, x + 1]))
                    lum = (r + gr + gb + b + 2) >> 2
                    out["ae_sum"][zy, zx] += lum
                    hist[lum >> 2] += 1
                    if max(r, gr, gb, b) < sat and min(r, gr, gb, b) >= dark:
                        out["awb_r"][zy, zx] += r
                        out["awb_g"][zy, zx] += gr + gb
                        out["awb_b"][zy, zx] += b
                        out["awb_n"][zy, zx] += 1
    out["ae_hist"] = hist
    return out


def test_bayer_stats_match_bruteforce():
    rng = np.random.default_rng(0)
    raw = rng.integers(0, 1024, (76, 100))
    grid = s3a.zone_grid(100, 76, 2, 4, 6, 4)
    got = s3a.bayer_stats(raw, "rggb", grid, 1000, 40)
    exp = brute_bayer(raw, grid, 1000, 40)
    for k in exp:
        assert (got[k] == exp[k]).all(), k


def test_af_stats_match_bruteforce():
    rng = np.random.default_rng(1)
    y = rng.integers(0, 256, (40, 72))
    grid = s3a.zone_grid(72, 40, 0, 0, 4, 2)
    p = np.pad(y, 1, mode="reflect")
    exp = np.zeros((16, 16), np.int64)
    for zy in range(16):
        for zx in range(16):
            for yy in range(zy * 2, zy * 2 + 2):
                for xx in range(zx * 4, zx * 4 + 4):
                    g = abs(int(p[yy + 1, xx + 2]) - int(p[yy + 1, xx])) + \
                        abs(int(p[yy + 2, xx + 1]) - int(p[yy, xx + 1]))
                    exp[zy, zx] += g if g > 10 else 0
    assert (s3a.af_stats(y, grid, 10)["af"] == exp).all()


def test_flat_field_stats():
    raw = np.full((64, 64), 300, np.int64)
    st = s3a.bayer_stats(raw, "rggb", (0, 0, 4, 4), 1020, 16)
    assert (st["awb_n"] == 4).all() and (st["awb_r"] == 1200).all() and (st["awb_g"] == 2400).all()
    assert st["ae_hist"][300 >> 2] == 32 * 32 and st["ae_hist"].sum() == 32 * 32
    assert (s3a.af_stats(np.full((64, 64), 77), (0, 0, 4, 4), 0)["af"] == 0).all()


def test_awb_gray_world_recovers_cast():
    rng = np.random.default_rng(2)
    scene = rng.integers(80, 700, (128, 128))           # neutral scene
    raw = scene.copy()
    raw[0::2, 0::2] = (scene[0::2, 0::2] * 0.6).astype(np.int64)   # R attenuated
    raw[1::2, 1::2] = (scene[1::2, 1::2] * 0.8).astype(np.int64)   # B attenuated
    st = s3a.bayer_stats(raw, "rggb", s3a.zone_grid(128, 128), 1020, 16)
    g = aaa.awb_gray_world(st)
    assert abs(g[0] / 1024 - 1 / 0.6) < 0.03 and abs(g[3] / 1024 - 1 / 0.8) < 0.03


def test_ae_closed_loop_converges():
    rng = np.random.default_rng(3)
    radiance = rng.uniform(0.02, 0.3, (128, 128))       # scene, linear
    grid = s3a.zone_grid(128, 128)
    state = aaa.AeState(exposure=8.0)                    # start 8x over-exposed
    for _ in range(12):
        raw = np.clip(radiance * state.exposure * 1023, 0, 1023).astype(np.int64)
        st = s3a.bayer_stats(raw, "rggb", grid, 1020, 16)
        aaa.ae_update(state, st, grid)
    raw = np.clip(radiance * state.exposure * 1023, 0, 1023).astype(np.int64)
    m = aaa.ae_mean(s3a.bayer_stats(raw, "rggb", grid, 1020, 16), grid)
    assert abs(m - state.target) / state.target < 0.05


def test_af_prefers_sharp_image():
    rng = np.random.default_rng(4)
    sharp = rng.integers(0, 256, (64, 64))
    blur = ((sharp + np.roll(sharp, 1, 0) + np.roll(sharp, 1, 1) + np.roll(sharp, (1, 1), (0, 1))) // 4)
    grid = s3a.zone_grid(64, 64)
    assert aaa.af_score(s3a.af_stats(sharp, grid, 4)) > 2 * aaa.af_score(s3a.af_stats(blur, grid, 4))


def test_pipeline_returns_stats():
    raw = np.fromfile(os.path.join(ROOT, "raw", "test.RAW"), dtype="<u2").reshape(1080, 1920).astype(np.int64)
    _, st, _ = run(raw[:256, :320], HwConfig(width=320, height=256))
    assert st["awb_r"].shape == (16, 16) and st["af"].shape == (16, 16) and st["ae_hist"].shape == (256,)
    assert st["ae_hist"].sum() == (16 * 20 // 2) * (16 * 16 // 2)   # every quad of the window counted
