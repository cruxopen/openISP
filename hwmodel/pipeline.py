# SPDX-License-Identifier: MIT
"""Full pipeline of the bit-accurate model, in hardware order:

  Bayer (10b): DPC -> BLC -> AAF -> AWB gain -> CNF
  RGB:         CFA (10b) -> CCM (10b) -> Gamma (10b -> 8b)
  YUV (8b):    CSC -> Y: NLM -> BNF -> EE -> BCC
                      UV: FCS (uses the EE edge map) -> HSC
3A statistics: AWB/AE on the BLC output (before the WB gains), AF on the CSC Y.
"""
from collections import OrderedDict

import numpy as np

from . import blocks as b
from . import stats as s3a
from .config import HwConfig


def stats_grid(cfg: HwConfig):
    return s3a.zone_grid(cfg.width, cfg.height, cfg.stats_x0, cfg.stats_y0, cfg.stats_zw, cfg.stats_zh)


def run(raw, cfg: HwConfig, keep_stages=False, gamma_lut=None, nlm_lut=None):
    """raw: (H, W) uint10 Bayer.  gamma_lut / nlm_lut: explicit tables (as
    loaded into the hardware); default: generated from cfg.gamma / cfg.nlm_h.
    Returns (yuv444 uint8 (H, W, 3), 3A statistics dict, stages dict)."""
    grid = stats_grid(cfg)
    st = OrderedDict()

    def keep(name, v):
        if keep_stages:
            st[name] = v
        return v

    x = keep("dpc", b.dpc(raw, cfg.dpc_thres, cfg.dpc_mode))
    x = keep("blc", b.blc(x, cfg.bayer_pattern, cfg.bl, cfg.bl_alpha, cfg.bl_beta))
    stats = s3a.bayer_stats(x, cfg.bayer_pattern, grid, cfg.awb_sat, cfg.awb_dark)
    x = keep("aaf", b.aaf(x))
    x = keep("awb", b.awb_gain(x, cfg.bayer_pattern, cfg.awb_gain))
    x = keep("cnf", b.cnf(x, cfg.bayer_pattern, cfg.awb_gain, cfg.cnf_thres))
    rgb = keep("cfa", b.cfa_malvar(x, cfg.bayer_pattern))
    rgb = keep("ccm", b.ccm(rgb, cfg.ccm))
    glut = b.gamma_lut(cfg.gamma) if gamma_lut is None else gamma_lut
    rgb8 = keep("gamma", b.gamma(rgb, glut))
    yuv = keep("csc", b.csc(rgb8, cfg.csc))
    y, uv = yuv[..., 0], yuv[..., 1:]
    stats.update(s3a.af_stats(y, grid, cfg.af_coring))
    nlut = b.nlm_lut(cfg.nlm_h, cfg.nlm_dshift) if nlm_lut is None else nlm_lut
    y = keep("nlm", b.nlm(y, nlut, cfg.nlm_search_r, cfg.nlm_dshift))
    y = keep("bnf", b.bnf(y, cfg.bnf_dw, cfg.bnf_rw, cfg.bnf_rthres))
    y, em = b.ee(y, cfg.edge_filter, cfg.ee_gain, cfg.ee_thres, cfg.ee_emclip)
    keep("ee", y)
    keep("edgemap", em)
    uv = keep("fcs", b.fcs(uv, em, cfg.fcs_edge, cfg.fcs_gain))
    uv = keep("hsc", b.hsc(uv, cfg.hue, cfg.saturation))
    y = keep("bcc", b.bcc(y, cfg.brightness, cfg.contrast))
    out = np.concatenate([y[..., None], uv], -1).astype(np.uint8)
    return out, stats, st


def yuv444_to_nv12(yuv):
    """4:4:4 -> NV12 with 2x2 averaging (round half up) of the chroma."""
    y = yuv[..., 0]
    uv = yuv[..., 1:].astype(np.int64)
    uv = (uv[0::2, 0::2] + uv[0::2, 1::2] + uv[1::2, 0::2] + uv[1::2, 1::2] + 2) >> 2
    return y.astype(np.uint8), uv.astype(np.uint8)


def yuv_to_rgb_preview(yuv):
    """BT.601 limited range YUV -> 8-bit RGB (float math, preview only)."""
    y = (yuv[..., 0].astype(np.float64) - 16) * 255 / 219
    u = (yuv[..., 1].astype(np.float64) - 128) * 255 / 224
    v = (yuv[..., 2].astype(np.float64) - 128) * 255 / 224
    rgb = np.stack([y + 1.402 * v, y - 0.344136 * u - 0.714136 * v, y + 1.772 * u], -1)
    return np.clip(rgb + 0.5, 0, 255).astype(np.uint8)
