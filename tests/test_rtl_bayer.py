# SPDX-License-Identifier: MIT
"""Bayer-domain RTL blocks vs hwmodel.blocks, bit for bit (data, TUSER, TLAST),
random configurations, frame sizes and flow control."""
import numpy as np
import pytest

from rtl_common import (FLOW, PATTERNS, StreamTB, bayer_scene, beats, cfa_code, check_stream,
                        frame_sizes, pack, rtlsim)
from hwmodel import blocks as b

pytestmark = pytest.mark.skipif(not rtlsim.available(), reason="no RTL simulator")

COMMON = ["rtl/common/pipe_out.sv", "rtl/common/row_parity.sv", "rtl/common/window_gen.sv", "rtl/common/axis_skid.sv"]


def rand_cfg(name, rng):
    pat = PATTERNS[int(rng.integers(4))]
    if name == "dpc":
        thres, mode = int(rng.integers(0, 200)), int(rng.integers(2))
        return pack([(thres, 10), (mode, 1)]), \
            lambda x: b.dpc(x, thres, "mean" if mode else "gradient")
    if name == "aaf":
        return 0, b.aaf
    if name == "blc":
        bl = [int(v) for v in rng.integers(0, 128, 4)]
        al, be = int(rng.integers(-300, 300)), int(rng.integers(-300, 300))
        return pack([(cfa_code(pat), 8)] + [(v, 10) for v in bl] + [(al, 10), (be, 10)]), \
            lambda x: b.blc(x, pat, bl, al, be)
    if name == "awb":
        g = [int(v) for v in rng.integers(512, 16384, 4)]
        return pack([(cfa_code(pat), 8)] + [(v, 14) for v in g]), \
            lambda x: b.awb_gain(x, pat, g)
    if name == "cnf":
        thres = int(rng.integers(0, 24))
        g = [int(rng.choice([900, 1024, 1100, 1229, 1500])), 1024, 1024,
             int(rng.choice([900, 1024, 1100, 1229, 1500]))]
        return pack([(cfa_code(pat), 8), (thres, 10), (g[0], 14), (g[3], 14)]), \
            lambda x: b.cnf(x, pat, g, thres)
    if name == "cfa":
        return pack([(cfa_code(pat), 8)]), lambda x: b.cfa_malvar(x, pat)
    raise KeyError(name)


#        name   source                     radius  out bits/comp, comps, cfg bits
BLOCKS = {"dpc": ("rtl/bayer/isp_dpc.sv", 2, 10, 1, 11),
          "aaf": ("rtl/bayer/isp_aaf.sv", 2, 10, 1, 1),
          "blc": ("rtl/bayer/isp_blc.sv", 0, 10, 1, 68),
          "awb": ("rtl/bayer/isp_awb.sv", 0, 10, 1, 64),
          "cnf": ("rtl/bayer/isp_cnf.sv", 4, 10, 1, 46),
          "cfa": ("rtl/bayer/isp_cfa.sv", 2, 10, 3, 8)}


@pytest.fixture(scope="module", params=list(BLOCKS))
def tb(request, tmp_path_factory):
    name = request.param
    src, r, bits, comps, cw = BLOCKS[name]
    t = StreamTB(str(tmp_path_factory.mktemp(name)), name, COMMON + [src],
                 iw=20, ow=2 * bits * comps, cw=cw)
    return name, t


@pytest.mark.parametrize("vpct,rpct,seed", FLOW)
def test_bayer_block_bit_exact(tb, vpct, rpct, seed):
    name, t = tb
    _, r, bits, comps, _ = BLOCKS[name]
    rng = np.random.default_rng(seed * 31 + len(name))
    sizes = frame_sizes(max(r, 1), rng)
    frames = [bayer_scene(h, w, rng) if i % 2 == 0 else rng.integers(0, 1024, (h, w))
              for i, (w, h) in enumerate(sizes)]
    changed = 0
    # one configuration per run (static registers), frames of different sizes
    cfg, model = rand_cfg(name, rng)
    exp = []
    for f in frames:
        o = model(f)
        changed += int((o != f).sum()) if o.shape == f.shape else 1
        exp.append((f.shape[1], f.shape[0], beats(o, bits)))
    ob, _, res = t.run([(f.shape[1], f.shape[0], beats(f, 10)) for f in frames], cfg=cfg,
                       junk=2 if r else 0, vpct=vpct, rpct=rpct, seed=seed)
    check_stream(ob, exp, res)
    assert changed > 0, "configuration did not exercise the block"
