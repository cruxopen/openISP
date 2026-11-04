# SPDX-License-Identifier: MIT
"""python -m hwmodel RAW [--width W --height H --pattern rggb --config cfg.csv] [-o out_dir]

RAW is little-endian uint16 Bayer, one sample per pixel (V4L2 SRGGB10 layout).
Writes <out>/<name>.png (preview), <name>.yuv444 and, with --stages, one
.npy per pipeline stage for block-level hardware verification."""
import argparse
import os

import numpy as np
from PIL import Image

from .config import HwConfig
from .pipeline import run, yuv_to_rgb_preview


def main():
    ap = argparse.ArgumentParser(prog="hwmodel")
    ap.add_argument("raw")
    ap.add_argument("--width", type=int)
    ap.add_argument("--height", type=int)
    ap.add_argument("--pattern")
    ap.add_argument("--config")
    ap.add_argument("--frame", type=int, default=0, help="frame index in a multi-frame file")
    ap.add_argument("--stages", action="store_true", help="dump every stage as .npy")
    ap.add_argument("-o", "--out", default="out")
    a = ap.parse_args()

    cfg = HwConfig.from_csv(a.config) if a.config else HwConfig()
    for k in ("width", "height"):
        if getattr(a, k):
            setattr(cfg, k, getattr(a, k))
    if a.pattern:
        cfg.bayer_pattern = a.pattern

    n = cfg.width * cfg.height
    raw = np.fromfile(a.raw, dtype="<u2", count=n, offset=a.frame * n * 2)
    raw = (raw & 0x3FF).reshape(cfg.height, cfg.width).astype(np.int64)

    yuv, stats, stages = run(raw, cfg, keep_stages=a.stages)
    os.makedirs(a.out, exist_ok=True)
    name = os.path.splitext(os.path.basename(a.raw))[0]
    yuv.tofile(os.path.join(a.out, name + ".yuv444"))
    Image.fromarray(yuv_to_rgb_preview(yuv)).save(os.path.join(a.out, name + ".png"))
    np.savez(os.path.join(a.out, name + ".stats.npz"), **stats)
    for k, v in stages.items():
        np.save(os.path.join(a.out, f"{name}.{k}.npy"), v)
    print(f"{name}: {cfg.width}x{cfg.height} -> {a.out}/{name}.png")


if __name__ == "__main__":
    main()
