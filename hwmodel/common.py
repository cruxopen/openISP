# SPDX-License-Identifier: MIT
"""Integer helpers shared by every block of the bit-accurate model.

Rules every block follows (and the HLS code must follow too):
  * all arithmetic is integer (numpy int64), no floats on the data path;
  * right shifts round half up:  rshr(x, s) = (x + 2**(s-1)) >> s
    (arithmetic shift, so negative values round toward +inf on .5);
  * divisions are exact integer divisions rounded half up (num >= 0, den > 0);
  * borders use mirror padding without edge repeat (numpy 'reflect'),
    which keeps the Bayer phase for even pad sizes.
"""
import numpy as np

I = np.int64


def rshr(x, s):
    """Round-half-up arithmetic right shift."""
    if s == 0:
        return x
    return (x + (1 << (s - 1))) >> s


def div_round(num, den):
    """Round-half-up integer division for num >= 0, den > 0 (elementwise)."""
    return (num + (den >> 1)) // den


def clip(x, lo, hi):
    return np.clip(x, lo, hi)


def pad(img, r):
    """Mirror padding without repeating the edge sample (numpy 'reflect')."""
    if img.ndim == 2:
        return np.pad(img, r, mode="reflect")
    return np.pad(img, ((r, r), (r, r), (0, 0)), mode="reflect")


class Win:
    """Neighbourhood access on a padded image: w(dy, dx) is the image shifted
    so that element [y, x] holds the original pixel at [y+dy, x+dx]."""

    def __init__(self, img, r):
        self.r = r
        self.h, self.w = img.shape[:2]
        self.p = pad(img.astype(I), r)

    def __call__(self, dy, dx):
        r = self.r
        return self.p[r + dy:r + dy + self.h, r + dx:r + dx + self.w]


# Bayer channel ids
R, GR, GB, B = 0, 1, 2, 3
_PATTERNS = {
    "rggb": ((R, GR), (GB, B)),
    "bggr": ((B, GB), (GR, R)),
    "grbg": ((GR, R), (B, GB)),
    "gbrg": ((GB, B), (R, GR)),
}


def cfa_map(pattern, h, w):
    """Per-pixel Bayer channel id (R/GR/GB/B) for a frame of size h x w."""
    tile = np.array(_PATTERNS[pattern], dtype=np.int8)
    return np.tile(tile, (h // 2 + 1, w // 2 + 1))[:h, :w]
