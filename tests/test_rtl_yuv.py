# SPDX-License-Identifier: MIT
"""YUV-domain RTL blocks (NLM, BNF, EE, FCS+HSC+BCC) vs hwmodel.blocks, bit for bit."""
import numpy as np
import pytest

from rtl_common import FLOW, StreamTB, beats, check_stream, frame_sizes, pack, rtlsim
from hwmodel import blocks as b
from hwmodel.config import HwConfig

pytestmark = pytest.mark.skipif(not rtlsim.available(), reason="no RTL simulator")

COMMON = ["rtl/common/pipe_out.sv", "rtl/common/window_gen.sv", "rtl/common/axis_skid.sv", "rtl/common/lut_mem.sv",
          "rtl/common/div_round_pipe.sv"]


def y_scene(h, w, rng):
    """Edges + texture + noise: exercises the range weights, EE coring and FCS ramp."""
    base = np.kron(rng.integers(0, 256, (h // 6 + 1, w // 6 + 1)), np.ones((6, 6), int))[:h, :w]
    return np.clip(base + rng.normal(0, rng.uniform(2, 20), (h, w)), 0, 255).astype(np.int64)


def cfg_nlm(rng, seed):
    h = int(rng.integers(5, 40)) if seed != 1 else HwConfig().nlm_h
    ds = int(rng.integers(0, 6)) if seed != 1 else 3
    lut = b.nlm_lut(h, ds)
    if seed == 3:
        lut = rng.integers(0, 256, 256)                      # arbitrary table, incl. zero weights
        lut[rng.random(256) < 0.3] = 0
    writes = [(a, int(v)) for a, v in enumerate(lut)]
    return pack([(ds, 3)]), writes, lambda y: b.nlm(y, lut, 2, ds)


def cfg_bnf(rng, seed):
    c = HwConfig()
    dw, rw, rt = c.bnf_dw, c.bnf_rw, c.bnf_rthres
    if seed != 1:
        dw = [[int(v) for v in rng.integers(0, 4096, 5)] for _ in range(5)]
        rw = [int(v) for v in rng.integers(0, 256, 4)]
        t = sorted(int(v) for v in rng.choice(255, 3, replace=False) + 1)[::-1]
        rt = t
        if seed == 3:
            rw[3] = 0; rw[2] = 0                              # zero weights near the centre
    fields = [(dw[i][j], 12) for i in range(5) for j in range(5)] + [(v, 8) for v in rw] + \
             [(v, 8) for v in rt]
    return pack(fields), (), lambda y: b.bnf(y, dw, rw, rt)


def cfg_ee(rng, seed):
    c = HwConfig()
    f, g, t, cl = c.edge_filter, c.ee_gain, c.ee_thres, c.ee_emclip
    if seed != 1:
        f = [[int(v) for v in rng.integers(-16, 17, 5)] for _ in range(3)]
        g = [int(v) for v in rng.integers(0, 600, 2)]
        t0 = int(rng.integers(0, 200)); t = [t0, t0 + int(rng.integers(0, 200))]
        lo = int(rng.integers(-300, 0)); cl = [lo, int(rng.integers(0, 300))]
    fields = [(f[i][j], 8) for i in range(3) for j in range(5)] + [(v, 10) for v in g] + \
             [(v, 16) for v in t] + [(v, 10) for v in cl]

    def model(y):
        yo, em = b.ee(y, f, g, t, cl)
        return np.stack([yo, np.clip(em, -2048, 2047)], -1)
    return pack(fields), (), model


@pytest.fixture(scope="module", params=["nlm", "bnf", "ee"])
def tb(request, tmp_path_factory):
    name = request.param
    ow = {"nlm": 16, "bnf": 16, "ee": 40}[name]
    cw = {"nlm": 3, "bnf": 356, "ee": 192}[name]
    t = StreamTB(str(tmp_path_factory.mktemp(name)), name, COMMON + [f"rtl/yuv/isp_{name}.sv"],
                 iw=16, ow=ow, cw=cw)
    return name, t


@pytest.mark.parametrize("vpct,rpct,seed", FLOW)
def test_y_block_bit_exact(tb, vpct, rpct, seed):
    name, t = tb
    rng = np.random.default_rng(seed * 7 + len(name))
    r = {"nlm": 3, "bnf": 2, "ee": 2}[name]
    sizes = frame_sizes(r, rng, n_rand=3, even_h=False)
    frames = [y_scene(h, w, rng) if i % 2 == 0 else rng.integers(0, 256, (h, w))
              for i, (w, h) in enumerate(sizes)]
    cfg, writes, model = {"nlm": cfg_nlm, "bnf": cfg_bnf, "ee": cfg_ee}[name](rng, seed)
    exp = []
    for f in frames:
        o = model(f)
        if name == "ee":   # beat = Y pixels then 12-bit edge map
            nb = []
            for v in zip(beats(o[..., 0], 8), beats(o[..., 1], 12)):
                nb.append(v[0] | v[1] << 16)
            exp.append((f.shape[1], f.shape[0], nb))
        else:
            exp.append((f.shape[1], f.shape[0], beats(o, 8)))
    ob, _, res = t.run([(f.shape[1], f.shape[0], beats(f, 8)) for f in frames], cfg=cfg,
                       writes=writes, junk=2, vpct=vpct, rpct=rpct, seed=seed)
    check_stream(ob, exp, res)


@pytest.fixture(scope="module")
def tb_post(tmp_path_factory):
    return StreamTB(str(tmp_path_factory.mktemp("post")), "yuvpost",
                    ["rtl/common/pipe_out.sv", "rtl/yuv/isp_yuvpost.sv"], iw=72, ow=48, cw=78)


@pytest.mark.parametrize("vpct,rpct,seed", FLOW)
def test_yuvpost_bit_exact(tb_post, vpct, rpct, seed):
    rng = np.random.default_rng(seed + 99)
    e0 = int(rng.integers(0, 1000)); k = int(rng.integers(0, 10))
    e1 = min(e0 + (1 << k), 2047)
    e0 = e1 - (1 << k)
    gain = int(rng.integers(0, 512)); hue = int(rng.integers(0, 360)); sat = int(rng.integers(0, 1024))
    br, ct = int(rng.integers(-255, 256)), int(rng.integers(-128, 128))
    if seed == 1:
        c = HwConfig(); e0, e1 = c.fcs_edge; gain, hue, sat, br, ct = c.fcs_gain, 30, 300, 10, 8
    sin_t, cos_t = b.hsc_tables()
    s, co = int(sin_t[hue]), int(cos_t[hue])
    cfg = pack([(e0, 11), (e1, 11), (gain, 9), (s, 10), (co, 10), (sat, 10), (br, 9), (ct, 8)])
    frames, exp = [], []
    for w, h in [(6, 1), (16, 3), (40, 4), (3840, 2)]:
        y = rng.integers(0, 256, (h, w)); uv = rng.integers(0, 256, (h, w, 2))
        em = rng.integers(-2048, 2048, (h, w))
        near = rng.random((h, w)) < 0.6
        em = np.where(near, rng.integers(-(e1 + 4), e1 + 5, (h, w)), em)
        bin_ = [a | bb << 16 | c << 40 for a, bb, c in zip(beats(y, 8), beats(em, 12), beats(uv, 8))]
        frames.append((w, h, bin_))
        uvo = b.hsc(b.fcs(uv, em, (e0, e1), gain), hue, sat)
        yo = b.bcc(y, br, ct)
        exp.append((w, h, beats(np.concatenate([yo[..., None], uvo], -1), 8)))
    ob, _, res = tb_post.run(frames, cfg=cfg, vpct=vpct, rpct=rpct, seed=seed)
    check_stream(ob, exp, res)
