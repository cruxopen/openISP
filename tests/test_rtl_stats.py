# SPDX-License-Identifier: MIT
"""3A statistics RTL vs hwmodel.stats, bit for bit, read back after every frame
(checks the double buffering and the histogram bank clearing)."""
import numpy as np
import pytest

from rtl_common import PATTERNS, StreamTB, bayer_scene, beats, cfa_code, check_stream, pack, rtlsim
from hwmodel import stats as s3a

pytestmark = pytest.mark.skipif(not rtlsim.available(), reason="no RTL simulator")

KEYS = ("awb_r", "awb_g", "awb_b", "awb_n", "ae_sum")


def rand_grid(w, h, rng):
    zw = 2 * int(rng.integers(1, w // 32 + 1))
    zh = 2 * int(rng.integers(1, h // 32 + 1))
    x0 = 2 * int(rng.integers(0, (w - 16 * zw) // 2 + 1))
    y0 = 2 * int(rng.integers(0, (h - 16 * zh) // 2 + 1))
    return x0, y0, zw, zh


SIZES = [(32, 32), (64, 40), (100, 76), (40, 34), (3840, 34), (96, 64)]


@pytest.fixture(scope="module")
def tb_bayer(tmp_path_factory):
    return StreamTB(str(tmp_path_factory.mktemp("stb")), "stats_bayer",
                    ["rtl/stats/zone_cnt.sv", "rtl/stats/zone_acc.sv", "rtl/stats/stats_bayer.sv"],
                    iw=20, ow=20, cw=80)


@pytest.fixture(scope="module")
def tb_af(tmp_path_factory):
    return StreamTB(str(tmp_path_factory.mktemp("saf")), "stats_af",
                    ["rtl/common/window_gen.sv", "rtl/common/axis_skid.sv", "rtl/stats/zone_cnt.sv", "rtl/stats/zone_acc.sv",
                     "rtl/stats/stats_af.sv"], iw=16, ow=16, cw=61)


@pytest.mark.parametrize("seed,vpct,rpct", [(1, 100, 100), (2, 60, 80), (3, 40, 50)])
def test_bayer_stats_bit_exact(tb_bayer, seed, vpct, rpct):
    rng = np.random.default_rng(seed)
    w0, h0 = SIZES[seed % len(SIZES)]
    pat = PATTERNS[seed % 4]
    sat, dark = int(rng.integers(700, 1024)), int(rng.integers(0, 200))
    grid = rand_grid(w0, h0, rng)
    cfg = pack([(cfa_code(pat), 8)] + [(v, 13) for v in grid] + [(sat, 10), (dark, 10)])
    # same size and grid for all frames (static registers); content changes
    frames = [bayer_scene(h0, w0, rng, defects=0.05) if i % 2 == 0 else rng.integers(0, 1024, (h0, w0))
              for i in range(5)]
    frames[3][:] = 300                                   # flat frame: few bins (clearing test)
    ob, rds, res = tb_bayer.run([(w0, h0, beats(f, 10)) for f in frames], cfg=cfg,
                                frame_reads=[[(0, 0x600), (0x600, 1)] for _ in frames],
                                vpct=vpct, rpct=rpct, seed=seed)
    check_stream(ob, [(w0, h0, beats(f, 10)) for f in frames], res)
    for i, f in enumerate(frames):
        exp = s3a.bayer_stats(f, pat, grid, sat, dark)
        rd = rds[i]
        assert rd[0x600] == i + 1
        for k, key in enumerate(KEYS):
            got = np.array([rd[k * 256 + z] for z in range(256)]).reshape(16, 16)
            assert (got == exp[key]).all(), (i, key)
        got_h = np.array([rd[0x500 + b] for b in range(256)])
        assert (got_h == exp["ae_hist"]).all(), (i, "hist")


@pytest.mark.parametrize("seed,vpct,rpct", [(1, 100, 100), (2, 60, 80), (3, 40, 50)])
def test_af_stats_bit_exact(tb_af, seed, vpct, rpct):
    rng = np.random.default_rng(seed + 10)
    w0, h0 = SIZES[(seed + 2) % len(SIZES)]
    grid = rand_grid(w0, h0, rng)
    coring = int(rng.integers(0, 40))
    cfg = pack([(v, 13) for v in grid] + [(coring, 9)])
    frames = [rng.integers(0, 256, (h0, w0)) for _ in range(4)]
    frames[1] = np.kron(rng.integers(0, 256, (h0 // 8 + 1, w0 // 8 + 1)), np.ones((8, 8), int))[:h0, :w0]
    ob, rds, res = tb_af.run([(w0, h0, beats(f, 8)) for f in frames], cfg=cfg,
                             frame_reads=[[(0, 256), (0x100, 1)] for _ in frames],
                             junk=2, vpct=vpct, rpct=rpct, seed=seed)
    check_stream(ob, [(w0, h0, beats(f, 8)) for f in frames], res)
    for i, f in enumerate(frames):
        exp = s3a.af_stats(f, grid, coring)["af"]
        got = np.array([rds[i][z] for z in range(256)]).reshape(16, 16)
        assert rds[i][0x100] == i + 1
        assert (got == exp).all(), i
