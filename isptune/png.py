# SPDX-License-Identifier: MIT
"""Minimal 8-bit RGB / gray PNG writer (no Pillow needed)."""
import struct
import zlib

import numpy as np


def write_png(path, img):
    a = np.ascontiguousarray(np.asarray(img, np.uint8))
    h, w = a.shape[:2]
    ctype = 2 if a.ndim == 3 else 0
    raw = b"".join(b"\x00" + a[y].tobytes() for y in range(h))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, ctype, 0, 0, 0)))
        f.write(chunk(b"IDAT", zlib.compress(raw, 6)))
        f.write(chunk(b"IEND", b""))
