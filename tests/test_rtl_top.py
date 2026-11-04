# SPDX-License-Identifier: MIT
"""Full RTL pipeline (isp_top) vs hwmodel.pipeline.run, bit for bit:
YUV444 output stream, AWB/AE statistics and AF statistics of every frame."""
import os

import numpy as np
import pytest

from rtl_common import FLOW, ROOT, StreamTB, bayer_scene, beats, check_stream, rtlsim
from hwmodel import regmap
from hwmodel.config import HwConfig
from hwmodel.pipeline import run

pytestmark = pytest.mark.skipif(not rtlsim.available(), reason="no RTL simulator")

RTL = ["rtl/common/" + f for f in ("pipe_out.sv", "row_parity.sv", "window_gen.sv", "lut_mem.sv",
                                   "div_round_pipe.sv", "fifo_fwft.sv", "axis_skid.sv")]
RTL += ["rtl/bayer/" + f for f in ("isp_dpc.sv", "isp_blc.sv", "isp_aaf.sv", "isp_awb.sv",
                                   "isp_cnf.sv", "isp_cfa.sv")]
RTL += ["rtl/rgb/isp_mat3.sv", "rtl/rgb/isp_gamma.sv"]
RTL += ["rtl/yuv/" + f for f in ("isp_nlm.sv", "isp_bnf.sv", "isp_ee.sv", "isp_yuvpost.sv", "isp_yuv.sv")]
RTL += ["rtl/stats/" + f for f in ("zone_cnt.sv", "zone_acc.sv", "stats_bayer.sv", "stats_af.sv")]
RTL += ["rtl/top/isp_regs.sv", "rtl/top/isp_ctrl.sv", "rtl/top/isp_top.sv"]
COMMIT = [(regmap.CTRL_REGS[2][1], regmap.CTRL_COMMIT)]
CTRL = regmap.CTRL_BASE
SB_KEYS = ("awb_r", "awb_g", "awb_b", "awb_n", "ae_sum")


@pytest.fixture(scope="module")
def tb(tmp_path_factory):
    return StreamTB(str(tmp_path_factory.mktemp("top")), "isp_top", RTL, iw=20, ow=48, cw=1)


def load_raw():
    return np.fromfile(os.path.join(ROOT, "raw", "test.RAW"), dtype="<u2").reshape(1080, 1920).astype(np.int64)


def rand_cfg(rng, w, h):
    c = HwConfig(width=w, height=h, bayer_pattern=str(rng.choice(["rggb", "bggr", "grbg", "gbrg"])))
    c.dpc_thres = int(rng.integers(10, 120)); c.dpc_mode = str(rng.choice(["gradient", "mean"]))
    c.bl = [int(v) for v in rng.integers(0, 64, 4)]
    c.bl_alpha, c.bl_beta = int(rng.integers(-40, 40)), int(rng.integers(-40, 40))
    c.awb_gain = [int(rng.integers(900, 2400)), 1024, 1024, int(rng.integers(900, 2400))]
    c.cnf_thres = int(rng.integers(0, 16))
    c.ccm = [[1400, -250, -126, 0], [-200, 1350, -126, 2048], [-50, -350, 1424, -1024]]
    c.gamma = float(rng.uniform(0.35, 0.7))
    c.nlm_h = int(rng.integers(6, 30)); c.nlm_dshift = int(rng.integers(2, 5))
    c.bnf_rw = [0, int(rng.integers(1, 16)), int(rng.integers(8, 32)), int(rng.integers(16, 64))]
    c.ee_gain = [int(rng.integers(0, 64)), int(rng.integers(64, 256))]
    c.fcs_edge = [24, 24 + (1 << int(rng.integers(2, 7)))]
    c.fcs_gain = int(rng.integers(128, 300))
    c.hue = int(rng.integers(0, 360)); c.saturation = int(rng.integers(128, 400))
    c.brightness = int(rng.integers(-20, 20)); c.contrast = int(rng.integers(-8, 8))
    c.af_coring = int(rng.integers(0, 16))
    return c


def run_frames(tb, raws, cfgs, vpct=100, rpct=100, seed=1):
    frames, writes, reads, exp, ref = [], [], [], [], []
    for raw, c in zip(raws, cfgs):
        h, w = raw.shape
        frames.append((w, h, beats(raw, 10)))
        writes.append(sorted(regmap.reg_words(c).items()) + regmap.table_writes(c) + COMMIT)
        reads.append([(regmap.STATS_BAYER_BASE, 0x601), (regmap.STATS_AF_BASE, 0x101), (CTRL, 9)])
        yuv, st, _ = run(raw, c)
        exp.append((w, h, beats(yuv, 8)))
        ref.append(st)
    ob, rds, res = tb.run(frames, frame_writes=writes, frame_reads=reads, vpct=vpct, rpct=rpct, seed=seed)
    check_stream(ob, exp, res)
    for i, st in enumerate(ref):
        rd = rds[i]
        for k, key in enumerate(SB_KEYS):
            got = np.array([rd[regmap.STATS_BAYER_BASE + k * 256 + z] for z in range(256)]).reshape(16, 16)
            assert (got == st[key]).all(), (i, key)
        got = np.array([rd[regmap.STATS_BAYER_BASE + 0x500 + v] for v in range(256)])
        assert (got == st["ae_hist"]).all(), (i, "hist")
        got = np.array([rd[regmap.STATS_AF_BASE + z] for z in range(256)]).reshape(16, 16)
        assert (got == st["af"]).all(), (i, "af")
        assert rd[regmap.STATS_BAYER_BASE + 0x600] == i + 1 and rd[regmap.STATS_AF_BASE + 0x100] == i + 1
        check_ctrl(rd, i)


def check_ctrl(rd, i, ist=7):
    """Control block after frame i (all frames separated by idle time)."""
    assert rd[CTRL + 0] == regmap.ISP_ID
    assert rd[CTRL + 1] == (regmap.VERSION_MAJOR << 24) | (regmap.VERSION_MINOR << 16) | 3840
    assert rd[CTRL + 2] == 0 and rd[CTRL + 3] == 0          # nothing pending, idle
    assert rd[CTRL + 4] == ist                               # FRAME_DONE | SOF | COMMIT_DONE
    assert rd[CTRL + 6] == i + 1 and rd[CTRL + 7] == i + 1   # frames in / out
    assert rd[CTRL + 8] == i                                 # commit applied before frame i


def test_top_openisp_raw_default_config(tb):
    raw = load_raw()
    crops = [raw[300:364, 600:696], raw[500:548, 1000:1064], raw[0:40, 0:48]]
    run_frames(tb, crops, [HwConfig(width=c.shape[1], height=c.shape[0]) for c in crops])


def test_top_full_width(tb):
    raw = load_raw()
    wide = np.concatenate([raw[400:434], raw[400:434, ::-1]], axis=1)       # 3840 x 34
    run_frames(tb, [wide], [HwConfig(width=3840, height=34)])


@pytest.mark.parametrize("seed,vpct,rpct", [(1, 100, 100), (2, 60, 70), (3, 35, 90)])
def test_top_random_config_flow_control(tb, seed, vpct, rpct):
    rng = np.random.default_rng(seed)
    raws, cfgs = [], []
    for w, h in [(64, 48), (96, 40)]:
        raws.append(bayer_scene(h, w, rng))
        cfgs.append(rand_cfg(rng, w, h))
    run_frames(tb, raws, cfgs, vpct=vpct, rpct=rpct, seed=seed)


def test_top_back_to_back_commit(tb):
    """Configuration of frame k+1 is written and committed while frame k is
    still in the pipeline (no idle time): every frame must still match its own
    configuration exactly (shadow registers + SOF hold).  Tables stay fixed."""
    rng = np.random.default_rng(7)
    raws, cfgs = [], []
    for w, h in [(64, 40), (64, 40), (48, 36)]:
        raws.append(bayer_scene(h, w, rng))
        c = rand_cfg(rng, w, h)
        c.gamma, c.nlm_h, c.nlm_dshift = 0.45, 15, 3
        cfgs.append(c)
    frames, writes, exp = [], [], []
    for i, (raw, c) in enumerate(zip(raws, cfgs)):
        h, w = raw.shape
        frames.append((w, h, beats(raw, 10)))
        wr = sorted(regmap.reg_words(c).items())
        if i == 0:
            wr += regmap.table_writes(c)
        writes.append(wr + COMMIT)
        exp.append((w, h, beats(run(raw, c)[0], 8)))
    ob, rd, res = tb.run(frames, frame_writes=writes, reads=[(CTRL, 9)], vpct=80, rpct=60, seed=7)
    check_stream(ob, exp, res)
    assert rd[CTRL + 6] == 3 and rd[CTRL + 7] == 3 and rd[CTRL + 8] == 2


def test_top_commit_hold_force_irq(tb):
    """HOLD delays a commit, FORCE applies it; IRQ enable / write-1-to-clear."""
    rng = np.random.default_rng(11)
    w, h = 48, 36
    raw = bayer_scene(h, w, rng)
    c0, c1 = HwConfig(width=w, height=h), HwConfig(width=w, height=h, saturation=380, brightness=9)
    ctl, ist, ien = CTRL + 2, CTRL + 4, CTRL + 5
    w0 = sorted(regmap.reg_words(c0).items()) + regmap.table_writes(c0) + COMMIT
    # frame 1: c1 written with HOLD -> stays pending, frame 1 still uses c0
    w1 = [(ctl, regmap.CTRL_HOLD)] + sorted(regmap.reg_words(c1).items()) + [(ctl, regmap.CTRL_COMMIT | regmap.CTRL_HOLD)]
    # frame 2: FORCE -> c1
    w2 = [(ctl, regmap.CTRL_FORCE)]
    r0 = [(CTRL, 9), ("W", ien, 1), (0xFFFE, 1), ("W", ist, 7), (ist, 1), (0xFFFF, 1)]
    r1 = [(CTRL, 9)]
    frames = [(w, h, beats(raw, 10))] * 3
    ob, rds, res = tb.run(frames, frame_writes=[w0, w1, w2], frame_reads=[r0, r1, [(CTRL, 9)]])
    exp = [(w, h, beats(run(raw, c)[0], 8)) for c in (c0, c0, c1)]
    check_stream(ob, exp, res)
    a, b_, c = rds
    check_ctrl(a, 0, ist=0)                           # IRQ_STATUS read after the clear
    assert a[0xFFFE] == 1 and a[0xFFFF] == 0          # irq high when enabled, low after W1C
    assert b_[ctl] == regmap.CTRL_COMMIT | regmap.CTRL_HOLD and b_[CTRL + 3] == 1   # pending
    assert b_[CTRL + 4] == 3                          # FRAME_DONE | SOF, no commit
    assert c[ctl] == 0 and c[CTRL + 4] == 7 and c[CTRL + 8] == 2


@pytest.mark.parametrize("vpct,rpct,seed", FLOW)
def test_top_reset_recovers_truncated_frame(tb, vpct, rpct, seed):
    """A truncated input frame (aborted stream, CSI-2 overflow) stays in the
    pipeline: FRAMES_IN != FRAMES_OUT would hold every later commit and SOF.
    CTRL.RESET flushes the datapath and clears the counters (registers kept):
    the next frame is bit exact again and counting restarts at 1."""
    rng = np.random.default_rng(40 + seed)
    w, h = 48, 36
    raw = bayer_scene(h, w, rng)
    c0 = HwConfig(width=w, height=h)
    c1 = HwConfig(width=w, height=h, saturation=330, contrast=40)
    ctl = CTRL + 2
    nb = w * h // 2
    full = beats(raw, 10)
    w0 = sorted(regmap.reg_words(c0).items()) + regmap.table_writes(c0) + COMMIT
    w2 = [(ctl, regmap.CTRL_RESET)] + sorted(regmap.reg_words(c1).items()) + COMMIT
    # frame 1 is cut after 18.2 lines; RESET + new configuration before frame 2
    frames = [(w, h, full), (w, h, full[:nb // 2 + 5], 0), (w, h, full)]
    ob, rds, res = tb.run(frames, frame_writes=[w0, [], w2], frame_reads=[[], None, [(CTRL, 9)]],
                          vpct=vpct, rpct=rpct, seed=seed, nout=2 * nb, idle=2000)
    assert "DONE" in res, res
    assert 2 * nb <= len(ob) < 3 * nb
    check_stream(ob[:nb], [(w, h, beats(run(raw, c0)[0], 8))], "DONE")
    check_stream(ob[-nb:], [(w, h, beats(run(raw, c1)[0], 8))], "DONE")
    rd = rds[-1]
    assert rd[CTRL + 6] == 1 and rd[CTRL + 7] == 1 and rd[CTRL + 3] == 0   # restarted, idle
    assert rd[CTRL + 1] >> 16 == 0x0101
