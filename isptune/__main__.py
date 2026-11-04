# SPDX-License-Identifier: MIT
"""python -m isptune <command> ...

  init      -o T.json [--width --height --pattern --sensor]   new tuning file
  blc       DARK.raw -t T.json --width W --height H           black level from a dark frame
  chart     CHART.raw -t T.json --corners x,y,x,y,x,y,x,y --illuminant D65 [--cct K]
                                                            WB gains + CCM of one illuminant
  noise     CHART.raw -t T.json --corners ...               denoise strength from the gray patches
  wb        WHITE.raw -t T.json --illuminant NAME --cct K    WB gains of one illuminant from a white
                                                            / grey target (no chart; CCM generic or kept)
  flat      FLAT1.raw FLAT2.raw ... -t T.json                noise model + denoise strength from flat
                                                            fields (>= 2 frames per file, several exposures)
  preview   RAW -t T.json [-o out.png] [--auto]              bit-exact offline rendering
  export    -t T.json -o BOARD.json [--gamma-curve srgb]    tables + validation, for the firmware
  check     -t T.json                                        validate and summarise
  live      [--host H|unix:PATH --port P] get NAME | set NAME V.. | state | stats | dump | push T.json
"""
import argparse
import json
import os
import sys

import numpy as np

from hwmodel.common import B, GB, GR, R, cfa_map

from . import calib, chart
from . import tuning as T


def _raw(a, t):
    w = a.width or t["isp"]["width"]
    h = a.height or t["isp"]["height"]
    raw = calib.load_raw(a.raw, w, h, a.frame)
    return raw, t["isp"]["bayer_pattern"] if not a.pattern else a.pattern


def _corners(s):
    v = [float(x) for x in s.split(",")]
    if len(v) != 8:
        raise argparse.ArgumentTypeError("--corners needs 8 numbers: TLx,TLy,TRx,TRy,BRx,BRy,BLx,BLy")
    return [(v[0], v[1]), (v[2], v[3]), (v[4], v[5]), (v[6], v[7])]


def cmd_init(a):
    t = T.default(a.width or 1920, a.height or 1080, a.pattern, a.sensor or "")
    T.save(t, a.out)
    print(f"wrote {a.out}")


def cmd_blc(a):
    t = T.load(a.tuning)
    raw, pat = _raw(a, t)
    r = calib.black_level(raw, pat)
    t["isp"]["bl"] = r["bl"]
    t["isp"]["dpc_thres"] = r["dpc_thres"]
    t["meta"]["blc"] = r
    T.save(t, a.tuning)
    print(f"black level {r['bl']}, read noise {[round(s, 2) for s in r['read_noise']]}, "
          f"hot pixels {r['hot_pixels']} ({r['hot_ppm']:.1f} ppm), dpc_thres {r['dpc_thres']}")


def cmd_chart(a):
    t = T.load(a.tuning)
    raw, pat = _raw(a, t)
    cct = a.cct or chart.ILLUMINANT_CCT.get(a.illuminant.upper())
    if not cct:
        sys.exit(f"unknown illuminant {a.illuminant}: give --cct")
    r = chart.calibrate(raw, pat, a.corners, t["isp"]["bl"], sat=a.sat)
    T.add_illuminant(t, {"name": a.illuminant, "cct": cct, "gains": r["gains"], "ccm": r["ccm"],
                         "ccm_source": "chart"})
    if a.set_static:          # use this illuminant as the static (AWB off) setting too
        t["isp"]["awb_gain"] = [r["gains"][0], 1024, 1024, r["gains"][1]]
        t["isp"]["ccm"] = r["ccm"]
    t["meta"].setdefault("chart", {})[a.illuminant] = r["report"]
    T.save(t, a.tuning)
    rep = r["report"]
    print(f"{a.illuminant} ({cct} K): gains r {r['gains'][0]} b {r['gains'][1]} (Q10), "
          f"{rep['patches_used']} patches")
    print(f"  CIEDE2000 WB only: mean {rep['de2000_wb_only']['mean']:.2f} max {rep['de2000_wb_only']['max']:.2f}")
    print(f"  CIEDE2000 + CCM : mean {rep['de2000_ccm']['mean']:.2f} max {rep['de2000_ccm']['max']:.2f}")
    for row in r["ccm"]:
        print("  ", row)


def cmd_noise(a):
    from hwmodel.pipeline import run
    t = T.load(a.tuning)
    raw, pat = _raw(a, t)
    cfg, g, n = T.hwconfig(t)
    cfg.width, cfg.height, cfg.bayer_pattern = raw.shape[1], raw.shape[0], pat
    # raw-domain noise model from the gray patches
    cen, pitch = chart.patch_centres(a.corners)
    half = max(int(pitch * 0.2), 2)
    ch = cfa_map(pat, *raw.shape)
    lv, var = [], []
    for i in chart.NEUTRALS:
        cx, cy = (int(round(v)) for v in cen[i])
        sl = (slice(cy - half, cy + half), slice(cx - half, cx + half))
        p, c = raw[sl].astype(float), ch[sl]
        for k in (R, GR, GB, B):
            v = p[c == k]
            lv.append(v.mean() - t["isp"]["bl"][k])
            var.append(v.var())
    gain, rvar = calib.noise_model(lv, var)
    # Y-domain noise after the pipeline with denoising off
    cfg.nlm_h, cfg.bnf_rw = 1, [0, 0, 0, 0]
    _, _, st = run(raw, cfg, keep_stages=True, gamma_lut=g)
    y = st["csc"][..., 0].astype(float)
    sig = [y[cy - half:cy + half, cx - half:cx + half].std()
           for cx, cy in (tuple(int(round(v)) for v in cen[i]) for i in chart.NEUTRALS[1:5])]
    s = float(np.median(sig))
    sug = calib.denoise_suggestion(s)
    t["isp"]["nlm_h"] = sug["nlm_h"]
    t["isp"]["bnf_rthres"] = sug["bnf_rthres"]
    t["isp"].pop("nlm_lut", None)
    t["meta"]["noise"] = {"shot_gain": gain, "read_var": rvar, "sigma_y": s, **sug}
    T.save(t, a.tuning)
    print(f"raw noise: var = {gain:.3f} x level + {rvar:.2f}; Y noise sigma {s:.2f} (8 bit)")
    print(f"nlm_h {sug['nlm_h']}, bnf_rthres {sug['bnf_rthres']}")


def cmd_wb(a):
    t = T.load(a.tuning)
    raw, pat = _raw(a, t)
    (gr, gb), m, _ = calib.flat_wb(raw, pat, t["isp"]["bl"], frac=a.roi, sat=a.sat)
    old = {i["name"]: i for i in t["awb"].get("illuminants", [])}.get(a.illuminant)
    ccm = old["ccm"] if old and old.get("ccm_source") == "chart" else calib.GENERIC_CCM
    ill = {"name": a.illuminant, "cct": a.cct, "gains": [gr, gb], "ccm": ccm,
           "ccm_source": "chart" if ccm is not calib.GENERIC_CCM else "generic"}
    T.add_illuminant(t, ill)
    if a.set_static:
        t["isp"]["awb_gain"] = [gr, 1024, 1024, gb]
        t["isp"]["ccm"] = ccm
    t["meta"].setdefault("wb", {})[a.illuminant] = {"channel_means": m, "cct": a.cct}
    T.save(t, a.tuning)
    print(f"{a.illuminant} ({a.cct} K): gains r {gr} b {gb} (Q10), channel means "
          f"{[round(v, 1) for v in m]}, CCM {ill['ccm_source']}")


def cmd_flat(a):
    from hwmodel.pipeline import run
    t = T.load(a.tuning)
    pairs, pat = [], None
    for path in a.raws:
        a.raw = path
        f1, pat = _raw(a, t)
        a.frame += 1
        f2, _ = _raw(a, t)
        a.frame -= 1
        pairs.append((f1, f2))
    bl = t["isp"]["bl"]
    lv, var = calib.flat_noise(pairs, pat, bl, frac=a.roi, sat=a.sat)
    if len(lv) < 8:
        sys.exit("need at least 2 unclipped exposures")
    gain, rvar = calib.noise_model(lv, var)
    # Y noise after the pipeline (denoising off) at the brightest unclipped pair
    cfg, g, _ = T.hwconfig(t)
    roi = calib.centre_roi(pairs[0][0].shape, min(a.roi, 0.25))
    best = max((p for p in pairs if (p[0] >= a.sat).mean() <= 0.001),
               key=lambda p: p[0][roi].mean())
    crop = [p[roi] for p in best]
    cfg.width, cfg.height, cfg.bayer_pattern = crop[0].shape[1], crop[0].shape[0], pat
    cfg.stats_x0 = cfg.stats_y0 = cfg.stats_zw = cfg.stats_zh = 0
    cfg.nlm_h, cfg.bnf_rw = 1, [0, 0, 0, 0]
    ys = [run(c, cfg, gamma_lut=g)[0][..., 0].astype(float) for c in crop]
    s = float((ys[0] - ys[1])[8:-8, 8:-8].std() / np.sqrt(2))
    sug = calib.denoise_suggestion(s)
    t["isp"]["nlm_h"] = sug["nlm_h"]
    t["isp"]["bnf_rthres"] = sug["bnf_rthres"]
    t["isp"].pop("nlm_lut", None)
    t["meta"]["noise"] = {"shot_gain": gain, "read_var": rvar, "sigma_y": s, "points": len(lv), **sug}
    T.save(t, a.tuning)
    print(f"raw noise: var = {gain:.3f} x level + {rvar:.2f} ({len(lv) // 4} exposures); "
          f"Y noise sigma {s:.2f} (8 bit, denoise off)")
    print(f"nlm_h {sug['nlm_h']}, bnf_rthres {sug['bnf_rthres']}")


def cmd_preview(a):
    from .png import write_png
    from .preview import process
    t = T.load(a.tuning)
    raw, pat = _raw(a, t)
    t["isp"]["width"], t["isp"]["height"], t["isp"]["bayer_pattern"] = raw.shape[1], raw.shape[0], pat
    rgb, yuv, st, info = process(raw, t, auto=a.auto)
    out = a.out or os.path.splitext(a.raw)[0] + ".png"
    write_png(out, rgb)
    if a.yuv:
        yuv.tofile(a.yuv)
    print(f"wrote {out}" + (f"  3A: {json.dumps(info)}" if info else ""))


def cmd_export(a):
    t = T.export(T.load(a.tuning), gamma_curve=a.gamma_curve)
    T.save(t, a.out)
    print(f"wrote {a.out} (gamma_lut + nlm_lut included, all registers in range)")


def cmd_check(a):
    t = T.load(a.tuning)
    cfg = T.check(t)
    ills = t["awb"].get("illuminants", [])
    print(f"ok: {cfg.width}x{cfg.height} {cfg.bayer_pattern}, black level {cfg.bl}, "
          f"{len(ills)} illuminant(s) {[i['name'] for i in ills]}, AE {'on' if t['ae']['enable'] else 'off'}, "
          f"AWB {'on' if t['awb']['enable'] else 'off'}")


def cmd_live(a):
    from .live import Live
    lv = Live(a.host, a.port)
    op = a.op
    if op[0] == "push":
        print(lv.push(T.export(T.load(op[1]))) or "ok")
    elif op[0] in ("state", "stats", "dump"):
        print(json.dumps(getattr(lv, op[0])(), indent=1))
    elif op[0] == "get":
        print(lv.get(op[1]))
    elif op[0] == "set":
        print(lv.set(op[1], " ".join(op[2:])) or "ok")
    else:
        print(lv.cmd(" ".join(op)))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="isptune", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)

    def raw_args(p):
        p.add_argument("raw")
        p.add_argument("-t", "--tuning", required=True)
        p.add_argument("--width", type=int)
        p.add_argument("--height", type=int)
        p.add_argument("--pattern")
        p.add_argument("--frame", type=int, default=0)

    p = sp.add_parser("init")
    p.add_argument("-o", "--out", required=True)
    p.add_argument("--width", type=int)
    p.add_argument("--height", type=int)
    p.add_argument("--pattern")
    p.add_argument("--sensor")
    p.set_defaults(fn=cmd_init)
    p = sp.add_parser("blc")
    raw_args(p)
    p.set_defaults(fn=cmd_blc)
    p = sp.add_parser("chart")
    raw_args(p)
    p.add_argument("--corners", type=_corners, required=True)
    p.add_argument("--illuminant", required=True)
    p.add_argument("--cct", type=float)
    p.add_argument("--sat", type=int, default=1000, help="raw level treated as clipped")
    p.add_argument("--set-static", action="store_true", help="also use as the AWB-off setting")
    p.set_defaults(fn=cmd_chart)
    p = sp.add_parser("noise")
    raw_args(p)
    p.add_argument("--corners", type=_corners, required=True)
    p.set_defaults(fn=cmd_noise)
    p = sp.add_parser("wb")
    raw_args(p)
    p.add_argument("--illuminant", required=True)
    p.add_argument("--cct", type=float, required=True)
    p.add_argument("--roi", type=float, default=0.3, help="central fraction of the frame used")
    p.add_argument("--sat", type=int, default=1000)
    p.add_argument("--set-static", action="store_true", help="also use as the AWB-off setting")
    p.set_defaults(fn=cmd_wb)
    p = sp.add_parser("flat")
    p.add_argument("raws", nargs="+")
    p.add_argument("-t", "--tuning", required=True)
    p.add_argument("--width", type=int)
    p.add_argument("--height", type=int)
    p.add_argument("--pattern")
    p.add_argument("--frame", type=int, default=0)
    p.add_argument("--roi", type=float, default=0.3)
    p.add_argument("--sat", type=int, default=1000)
    p.set_defaults(fn=cmd_flat)
    p = sp.add_parser("preview")
    raw_args(p)
    p.add_argument("-o", "--out")
    p.add_argument("--yuv", help="also write the YUV444 output")
    p.add_argument("--auto", action="store_true", help="run AWB / AE (digital gain) first")
    p.set_defaults(fn=cmd_preview)
    p = sp.add_parser("export")
    p.add_argument("-t", "--tuning", required=True)
    p.add_argument("-o", "--out", required=True)
    p.add_argument("--gamma-curve", choices=["srgb"])
    p.set_defaults(fn=cmd_export)
    p = sp.add_parser("check")
    p.add_argument("-t", "--tuning", required=True)
    p.set_defaults(fn=cmd_check)
    p = sp.add_parser("live")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=5555)
    p.add_argument("op", nargs="+")
    p.set_defaults(fn=cmd_live)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
