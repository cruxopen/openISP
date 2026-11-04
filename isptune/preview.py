# SPDX-License-Identifier: MIT
"""Offline preview: the bit-exact hardware model (hwmodel.pipeline) with the
parameters of a tuning file, optionally after a few iterations of the
firmware 3A algorithms (hwmodel.aaa reference = sw/lib/oisp_aaa.c), so the
picture is what the board will show for the same RAW frame."""
import copy

import numpy as np

from hwmodel import aaa
from hwmodel.pipeline import run, stats_grid, yuv_to_rgb_preview

from . import tuning as T


def process(raw, t, auto=False, iterations=2):
    """-> (rgb8 preview, yuv444, stats, info).  auto: run AWB (incl. CCM
    interpolation) and AE as digital gain only (exposure is fixed offline)."""
    t = copy.deepcopy(t)
    cfg, g, n = T.hwconfig(t)
    info = {}
    if auto:
        grid = stats_grid(cfg)
        awb = dict(t["awb"], speed=1.0)
        st_awb = aaa.AwbFwState()
        dg = 1.0
        for _ in range(iterations):
            _, st, _ = run(raw, cfg, gamma_lut=g, nlm_lut=n)
            r = aaa.awb_step(st_awb, st, awb) if t["awb"]["enable"] else None
            gr, gb = (r[0], r[1]) if r else (cfg.awb_gain[0], cfg.awb_gain[3])
            if r and r[2]:
                cfg.ccm = r[2]
            if t["ae"]["enable"]:
                m = max(aaa.ae_measure(st, grid), 1.0)
                dg = float(np.clip(t["ae"]["target"] / m, 0.25, max(t["ae"]["dgain_max"], 1.0)))
            cfg.awb_gain = aaa.wb_registers(gr, gb, dg)
            info = {"awb_gain": cfg.awb_gain, "ccm": cfg.ccm, "cct": r[3] if r else None, "dgain": dg}
    yuv, st, _ = run(raw, cfg, gamma_lut=g, nlm_lut=n)
    return yuv_to_rgb_preview(yuv), yuv, st, info
