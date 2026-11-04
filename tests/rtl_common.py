# SPDX-License-Identifier: MIT
"""Shared helpers of the RTL block tests."""
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "hw", "sim"))
sys.path.insert(0, ROOT)

import rtlsim                                    # noqa: E402,F401
from streamtb import PPC, StreamTB, beats, check_stream, pack, unbeats   # noqa: E402,F401
from hwmodel.common import _PATTERNS             # noqa: E402

PATTERNS = ("rggb", "bggr", "grbg", "gbrg")
FLOW = [(100, 100, 1), (60, 70, 2), (35, 90, 3)]   # valid %, ready %, seed


def cfa_code(pattern):
    """Channel id of pixel (yodd, xodd) at bits [2*(2*yodd + xodd) +: 2]."""
    t = _PATTERNS[pattern]
    return sum(t[y][x] << (2 * (2 * y + x)) for y in (0, 1) for x in (0, 1))


def bayer_scene(h, w, rng, defects=0.02, maxv=1023):
    """Smooth colour scene + noise + hot/cold pixels (exercises DPC/CNF paths)."""
    bh, bw = h // 4 + 2, w // 4 + 2
    base = rng.uniform(0.05, 0.9, (bh, bw))
    base = np.kron(base, np.ones((4, 4)))[:h, :w]
    gains = rng.uniform(0.4, 1.0, 4)
    ch = np.zeros((h, w), int)
    ch[0::2, 1::2] = 1; ch[1::2, 0::2] = 2; ch[1::2, 1::2] = 3
    img = base * gains[ch] * maxv + rng.normal(0, 12, (h, w))
    m = rng.random((h, w)) < defects
    img[m] = rng.choice([0, maxv], m.sum())
    return np.clip(img, 0, maxv).astype(np.int64)


def frame_sizes(r, rng, n_rand=4, maxw=3840, even_h=True):
    """(w, h) list: minimum legal, small heights, random, full width.
    Bayer frames (even_h) always have an even height."""
    wmin = max(3 * PPC, ((r + 1 + 1) // 2) * 2)
    sizes = [(wmin, r + 1), (wmin + 2, r + 2), (16, 2 * r + 3)]
    for _ in range(n_rand):
        sizes.append((int(rng.integers(wmin // 2, 40)) * 2, int(rng.integers(r + 1, 20)) // 2 * 2))
    sizes.append((maxw, max(r + 1, 4)))
    out = []
    for w, h in sizes:
        h = max(h, r + 1)
        if even_h:
            h += h & 1
        out.append((w, h))
    return out
