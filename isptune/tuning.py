# SPDX-License-Identifier: MIT
"""Tuning file: the JSON document shared by this tool, the firmware
(sw/lib/oisp_tuning.c) and the offline preview.

    {"version": 1,
     "sensor": {"name": "ar1335", ...},
     "isp":    {<hwmodel.config.HwConfig fields>, "gamma_lut": [...], "nlm_lut": [...]},
     "ae":     {<hwmodel.aaa.AE_DEFAULTS keys>},
     "awb":    {<hwmodel.aaa.AWB_DEFAULTS keys>, "illuminants": [
                  {"name", "cct", "gains": [r, b] (Q10), "ccm": 3x4 Q10}, ...]},
     "af":     {"enable", "roi", "pos_min", "pos_max", "step"},
     "meta":   {free-form calibration notes}}
"""
import copy
import dataclasses
import json

import numpy as np

from hwmodel import aaa, regmap
from hwmodel import blocks as b
from hwmodel.config import HwConfig

AF_DEFAULTS = dict(enable=False, roi=[4, 12, 4, 12], pos_min=0, pos_max=1023, step=32)
TABLES = ("gamma_lut", "nlm_lut")


# Sensor profiles, measured on the board with the stock KV260 driver.
# AR1335 (J8): exposure is the private control "coarse_integration_time"
# (lines, register 0x0202); "gain" 0..5 is an index into global_gain (0x305E)
# settings, 2x per step (relative to step 0). Step 5 is not used: its black
# level drops from ~42 to ~3 DN (dark frames), the sensor digital gain clips.
# Integration limit = frame length - 32 (the driver's own default), frame
# length 3150 lines at 4K30, 1573 at 1080p.
SENSORS = {
    "ar1335": dict(
        sensor={"exposure_ctrl": "coarse_integration_time", "gain_ctrl": "gain"},
        ae={"gain_table": [1.0, 2.0, 4.0, 8.0, 16.0, 32.0], "gain_code_min": 0, "gain_code_max": 4},
        frame_lines={(3840, 2160): 3150, (1920, 1080): 1573},
        pattern="rggb"),
}


def default(width=1920, height=1080, pattern=None, sensor=""):
    prof = SENSORS.get(sensor.lower(), {})
    cfg = HwConfig(width=width, height=height, bayer_pattern=pattern or prof.get("pattern", "rggb"))
    t = {"version": 1, "sensor": {"name": sensor, **prof.get("sensor", {})}, "isp": dataclasses.asdict(cfg),
         "ae": dict(aaa.AE_DEFAULTS, **prof.get("ae", {})), "awb": copy.deepcopy(aaa.AWB_DEFAULTS),
         "af": dict(AF_DEFAULTS), "meta": {}}
    fl = prof.get("frame_lines", {}).get((width, height))
    if fl:
        t["ae"]["lines_max"] = fl - 32
    return t


def load(path):
    with open(path) as f:
        t = json.load(f)
    base = default()
    for sec in ("isp", "ae", "awb", "af"):
        base[sec].update(t.get(sec, {}))
    for k, v in t.items():
        if k not in ("isp", "ae", "awb", "af"):
            base[k] = v
    return base


def save(t, path):
    def fmt(o, ind=0):
        """Compact JSON: one line per matrix row / table chunk."""
        pad = "  " * ind
        if isinstance(o, dict):
            items = [f'{pad}  {json.dumps(k)}: {fmt(v, ind + 1).lstrip()}' for k, v in o.items()]
            return pad + "{\n" + ",\n".join(items) + "\n" + pad + "}" if items else pad + "{}"
        if isinstance(o, list) and o and isinstance(o[0], list):
            return pad + "[" + ", ".join(json.dumps(r) for r in o) + "]"
        if isinstance(o, list) and len(o) > 16 and all(isinstance(x, int) for x in o):
            rows = [", ".join(str(x) for x in o[i:i + 32]) for i in range(0, len(o), 32)]
            return pad + "[\n" + ",\n".join(pad + "    " + r for r in rows) + "]"
        if isinstance(o, list) and o and isinstance(o[0], dict):
            return pad + "[\n" + ",\n".join(fmt(x, ind + 1) for x in o) + "]"
        return pad + json.dumps(o)
    with open(path, "w") as f:
        f.write(fmt(t) + "\n")


def hwconfig(t):
    """isp section -> (HwConfig, gamma_lut or None, nlm_lut or None)."""
    isp = dict(t["isp"])
    tabs = {k: isp.pop(k, None) for k in TABLES}
    names = {f.name for f in dataclasses.fields(HwConfig)}
    bad = set(isp) - names
    if bad:
        raise KeyError(f"unknown isp parameters: {sorted(bad)}")
    cfg = HwConfig(**isp)
    g = None if tabs["gamma_lut"] is None else np.asarray(tabs["gamma_lut"], np.int64)
    n = None if tabs["nlm_lut"] is None else np.asarray(tabs["nlm_lut"], np.int64)
    return cfg, g, n


def set_isp(t, cfg: HwConfig):
    tabs = {k: t["isp"][k] for k in TABLES if k in t["isp"]}
    t["isp"] = dataclasses.asdict(cfg)
    t["isp"].update(tabs)


def srgb_gamma_lut():
    """10-bit linear -> 8-bit sRGB transfer curve (IEC 61966-2-1)."""
    x = np.arange(1024) / 1023.0
    y = np.where(x <= 0.0031308, 12.92 * x, 1.055 * x ** (1 / 2.4) - 0.055)
    return np.floor(255.0 * y + 0.5).astype(np.int64)


def export(t, gamma_curve=None):
    """Materialise the hardware tables (what the firmware loads) and validate
    every register value.  gamma_curve: None (from isp.gamma), "srgb"."""
    t = copy.deepcopy(t)
    cfg, g, n = hwconfig(t)
    if gamma_curve == "srgb":
        g = srgb_gamma_lut()
    elif g is None:
        g = b.gamma_lut(cfg.gamma)
    if n is None:
        n = b.nlm_lut(cfg.nlm_h, cfg.nlm_dshift)
    t["isp"]["gamma_lut"] = [int(v) for v in g]
    t["isp"]["nlm_lut"] = [int(v) for v in n]
    check(t)
    return t


def check(t):
    """Raise ValueError if a register does not fit or a 3A setting is invalid."""
    cfg, g, n = hwconfig(t)
    regmap.reg_words(cfg)
    for name, tab, size in (("gamma_lut", g, 1024), ("nlm_lut", n, 256)):
        if tab is not None and (len(tab) != size or tab.min() < 0 or tab.max() > 255):
            raise ValueError(f"{name}: {size} values in 0..255 expected")
    e0, e1 = cfg.fcs_edge
    if e1 <= e0 or (e1 - e0) & (e1 - e0 - 1) or e1 > 2047:
        raise ValueError("fcs_edge: e1 - e0 must be a power of two and e1 <= 2047")
    ills = t["awb"].get("illuminants", [])
    if [i["cct"] for i in ills] != sorted(i["cct"] for i in ills):
        raise ValueError("awb.illuminants must be sorted by cct")
    for i in ills:
        if len(i["gains"]) != 2 or np.asarray(i["ccm"]).shape != (3, 4):
            raise ValueError(f"illuminant {i.get('name')}: gains [r, b] and a 3x4 ccm expected")
    ae = t["ae"]
    tab = ae.get("gain_table") or []
    if not 0 <= ae["gain_code_min"] <= ae["gain_code_max"] or not 0 < ae["lines_min"] <= ae["lines_max"] \
            or (not tab and ae["gain_code_min"] == 0):
        raise ValueError("ae: inconsistent exposure / gain limits")
    if tab and (any(g <= 0 for g in tab) or tab != sorted(tab) or ae["gain_code_max"] >= len(tab)):
        raise ValueError("ae.gain_table: positive, non-decreasing, covering gain_code_max")
    return cfg


def add_illuminant(t, ill):
    """Insert or replace (same name) an illuminant, keeping the CCT order."""
    ills = [i for i in t["awb"].get("illuminants", []) if i["name"] != ill["name"]]
    ills.append(ill)
    t["awb"]["illuminants"] = sorted(ills, key=lambda i: i["cct"])
