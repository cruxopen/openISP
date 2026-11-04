# SPDX-License-Identifier: MIT
"""RGB-domain RTL blocks (CCM, gamma, CSC) vs hwmodel.blocks, bit for bit."""
import numpy as np
import pytest

from rtl_common import FLOW, StreamTB, beats, check_stream, pack, rtlsim
from hwmodel import blocks as b
from hwmodel.config import HwConfig

pytestmark = pytest.mark.skipif(not rtlsim.available(), reason="no RTL simulator")

COMMON = ["rtl/common/pipe_out.sv", "rtl/common/lut_mem.sv"]
SIZES = [(6, 1), (16, 3), (40, 5), (3840, 2)]


def mat_cfg(m):
    return pack([(m[k][c], 16) for k in range(3) for c in range(3)] + [(m[k][3], 22) for k in range(3)])


def rand_matrix(rng, ib):
    m = [[int(v) for v in rng.integers(-2048, 3072, 3)] for _ in range(3)]
    for k in range(3):
        m[k].append(int(rng.integers(-(1 << ib) * 512, (1 << ib) * 512)))
    return m


@pytest.fixture(scope="module", params=["ccm", "csc", "gamma"])
def tb(request, tmp_path_factory):
    name = request.param
    src = "rtl/rgb/isp_gamma.sv" if name == "gamma" else "rtl/rgb/isp_mat3.sv"
    ib = 8 if name == "csc" else 10
    ob = 10 if name == "ccm" else 8
    t = StreamTB(str(tmp_path_factory.mktemp(name)), name, COMMON + [src],
                 iw=2 * 3 * ib, ow=2 * 3 * ob, cw=210)
    return name, t, ib, ob


@pytest.mark.parametrize("vpct,rpct,seed", FLOW)
def test_rgb_block_bit_exact(tb, vpct, rpct, seed):
    name, t, ib, ob = tb
    rng = np.random.default_rng(seed + 17 * len(name))
    frames = [rng.integers(0, 1 << ib, (h, w, 3)) for w, h in SIZES]
    cfg, writes, frame_writes = 0, (), None
    if name == "ccm":
        m = rand_matrix(rng, 10)
        model = lambda x: b.ccm(x, m)                       # noqa: E731
        cfg = mat_cfg(m)
    elif name == "csc":
        m = HwConfig().csc if seed == 1 else rand_matrix(rng, 8)
        model = lambda x: b.csc(x, m)                       # noqa: E731
        cfg = mat_cfg(m)
    else:
        # a different table per frame: checks table reload between frames
        luts = [b.gamma_lut(float(g)) for g in rng.uniform(0.3, 2.2, len(frames))]
        frame_writes = [[(a, int(v)) for a, v in enumerate(lut)] for lut in luts]
        model = None
    exp = []
    for i, f in enumerate(frames):
        o = b.gamma(f, luts[i]) if name == "gamma" else model(f)
        exp.append((f.shape[1], f.shape[0], beats(o, ob)))
    ob_, _, res = t.run([(f.shape[1], f.shape[0], beats(f, ib)) for f in frames], cfg=cfg,
                        writes=writes, frame_writes=frame_writes, vpct=vpct, rpct=rpct, seed=seed)
    check_stream(ob_, exp, res)
