# SPDX-License-Identifier: MIT
"""Tests for the bit-accurate model.

* property tests: identities, flat-field invariance, known values;
* equivalence tests: blocks whose semantics were kept are compared against
  the original floating-point openISP modules in model/ on a real crop.
"""
import contextlib
import io
import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hwmodel import blocks as b                      # noqa: E402
from hwmodel.common import rshr, cfa_map, R, B       # noqa: E402
from hwmodel.config import HwConfig                  # noqa: E402
from hwmodel.pipeline import run, yuv444_to_nv12     # noqa: E402


@pytest.fixture(scope="module")
def raw():
    r = np.fromfile(os.path.join(ROOT, "raw", "test.RAW"), dtype="<u2").reshape(1080, 1920)
    return r.astype(np.int64)


@pytest.fixture(scope="module")
def crop(raw):
    return raw[500:564, 900:964].copy()  # 64x64, even offsets keep RGGB phase


def quiet(fn, *a):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a)


# ------------------------------------------------------------------ helpers
def test_rshr_rounds_half_up():
    x = np.array([-3, -2, -1, 0, 1, 2, 3])
    assert rshr(x, 1).tolist() == [-1, -1, 0, 0, 1, 1, 2]


def test_cfa_map_rggb():
    m = cfa_map("rggb", 4, 4)
    assert m[0, 0] == R and m[1, 1] == B and m[2, 2] == R


# ------------------------------------------------------------------ properties
def test_flat_bayer_passes_bayer_blocks_unchanged():
    flat = np.full((32, 32), 400, np.int64)
    assert (b.dpc(flat, 30) == flat).all()
    assert (b.aaf(flat) == flat).all()
    assert (b.cnf(flat, "rggb", [1024] * 4) == flat).all()
    assert (b.cfa_malvar(flat, "rggb") == 400).all()


def test_dpc_fixes_hot_pixel():
    img = np.full((16, 16), 200, np.int64)
    img[8, 8] = 1000
    out = b.dpc(img, 30)
    assert out[8, 8] == 200 and (np.delete(out.ravel(), 8 * 16 + 8) == 200).all()


def test_ccm_identity_and_awb_unity():
    rgb = np.random.default_rng(0).integers(0, 1024, (8, 8, 3))
    eye = [[1024, 0, 0, 0], [0, 1024, 0, 0], [0, 0, 1024, 0]]
    assert (b.ccm(rgb, eye) == rgb).all()
    assert (b.awb_gain(rgb[..., 0], "rggb", [1024] * 4) == rgb[..., 0]).all()


def test_csc_bt601_limited_range_endpoints():
    m = HwConfig().csc
    white = b.csc(np.full((1, 1, 3), 255), m)[0, 0]
    black = b.csc(np.zeros((1, 1, 3), np.int64), m)[0, 0]
    assert abs(int(white[0]) - 235) <= 1 and abs(int(black[0]) - 16) <= 1
    assert all(abs(int(v) - 128) <= 1 for v in (*white[1:], *black[1:]))


def test_yuv_filters_keep_flat_y():
    y = np.full((24, 24), 120, np.int64)
    cfg = HwConfig()
    assert (b.nlm(y, b.nlm_lut(cfg.nlm_h)) == 120).all()
    assert (b.bnf(y, cfg.bnf_dw, cfg.bnf_rw, cfg.bnf_rthres) == 120).all()
    ye, em = b.ee(y, cfg.edge_filter, cfg.ee_gain, cfg.ee_thres, cfg.ee_emclip)
    assert (ye == 120).all() and (em == 0).all()


def test_chroma_controls_identity():
    uv = np.random.default_rng(1).integers(0, 256, (8, 8, 2))
    assert (b.hsc(uv, 0, 256) == uv).all()
    assert (b.fcs(uv, np.zeros((8, 8), np.int64), [32, 64], 256) == uv).all()
    y = np.arange(256).reshape(16, 16)
    assert (b.bcc(y, 0, 0) == y).all()


def test_nlm_reduces_noise():
    rng = np.random.default_rng(2)
    y = np.clip(128 + rng.normal(0, 6, (64, 64)), 0, 255).round().astype(np.int64)
    out = b.nlm(y, b.nlm_lut(15))
    assert out.std() < 0.6 * y.std()


def test_pipeline_is_deterministic_and_typed(crop):
    a, _, _ = run(crop, HwConfig(width=64, height=64))
    c, _, st = run(crop, HwConfig(width=64, height=64), keep_stages=True)
    assert a.dtype == np.uint8 and a.shape == (64, 64, 3) and (a == c).all()
    assert list(st)[0] == "dpc" and list(st)[-1] == "bcc"
    y, uv = yuv444_to_nv12(a)
    assert y.shape == (64, 64) and uv.shape == (32, 32, 2)


def test_config_csv_roundtrip(tmp_path):
    cfg = HwConfig(awb_gain=[1500, 1024, 1024, 1300], hue=10)
    p = tmp_path / "c.csv"
    cfg.to_csv(p)
    assert HwConfig.from_csv(p) == cfg


# ------------------------------------------------------------------ equivalence with openISP
def test_dpc_matches_openisp(crop):
    from model.dpc import DPC
    ref = quiet(DPC(crop.astype(np.uint16), 30, "gradient", 1023).execute)
    assert (b.dpc(crop, 30, "gradient") == ref).all()


def test_cfa_matches_openisp_within_rounding(crop):
    from model.cfa import CFA
    ref = quiet(CFA(crop.astype(np.uint16), "malvar", "rggb", 1023).execute).astype(np.int64)
    diff = np.abs(b.cfa_malvar(crop, "rggb") - ref)
    assert diff.max() <= 1, diff.max()   # openISP truncates, the model rounds


def test_bnf_matches_openisp_within_rounding(crop):
    from model.bnf import BNF
    cfg = HwConfig()
    y = (crop >> 2)
    ref = quiet(BNF(y.astype(np.uint16), np.array(cfg.bnf_dw), cfg.bnf_rw, cfg.bnf_rthres, 255).execute)
    diff = np.abs(b.bnf(y, cfg.bnf_dw, cfg.bnf_rw, cfg.bnf_rthres) - ref.astype(np.int64))
    assert diff.max() <= 1, diff.max()


def test_aaf_matches_openisp_inside_border(crop):
    from model.aaf import AAF
    ref = quiet(AAF(crop.astype(np.float64)).execute)   # scipy border mode differs: compare interior
    diff = np.abs(b.aaf(crop) - ref)[2:-2, 2:-2]
    assert diff.max() <= 0.5


def test_csc_matches_openisp_within_rounding():
    from model.csc import CSC
    rgb = np.random.default_rng(3).integers(0, 256, (16, 16, 3))
    f = np.array([[0.257, 0.504, 0.098, 16], [-0.148, -0.291, 0.439, 128], [0.439, -0.368, -0.071, 128]])
    ref = CSC(rgb.astype(np.float64), f * 1024).execute().astype(np.int64)
    ours = b.csc(rgb, HwConfig().csc)
    assert np.abs(ours[..., 0] - ref[..., 0]).max() <= 1   # Y is always in range
