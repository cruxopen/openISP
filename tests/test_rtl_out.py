# SPDX-License-Identifier: MIT
"""Output packing isp_yuyv (YUV 4:4:4 -> YUYV for the frame buffer) vs
hwmodel.blocks.yuyv, bit for bit, with random flow control."""
import numpy as np
import pytest

from rtl_common import FLOW, StreamTB, beats, check_stream, rtlsim
from hwmodel import blocks as b

pytestmark = pytest.mark.skipif(not rtlsim.available(), reason="no RTL simulator")


def test_yuyv_model_layout():
    yuv = np.array([[[10, 100, 200], [20, 103, 50], [30, 0, 255], [40, 255, 0]]], np.uint8)
    out = b.yuyv(yuv)
    # memory order Y0 U Y1 V
    assert out.reshape(-1).tolist() == [10, 102, 20, 125, 30, 128, 40, 128]


@pytest.fixture(scope="module")
def tb(tmp_path_factory):
    return StreamTB(str(tmp_path_factory.mktemp("yuyv")), "yuyv", ["rtl/top/isp_yuyv.sv"], iw=48, ow=48, cw=8)


@pytest.mark.parametrize("vpct,rpct,seed", FLOW)
def test_yuyv_bit_exact(tb, vpct, rpct, seed):
    rng = np.random.default_rng(seed)
    frames = [rng.integers(0, 256, (h, w, 3)) for w, h in [(6, 1), (16, 3), (40, 5), (3840, 2)]]
    frames[0][0, :, 1:] = 255                       # rounding at the top of the range
    ins = [(f.shape[1], f.shape[0], beats(f, 8)) for f in frames]
    exp = [(f.shape[1], f.shape[0], beats(b.yuyv(f), 8)) for f in frames]
    ob, _, res = tb.run(ins, vpct=vpct, rpct=rpct, seed=seed)
    check_stream(ob, exp, res)
