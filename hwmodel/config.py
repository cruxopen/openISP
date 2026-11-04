# SPDX-License-Identifier: MIT
"""Parameters of the hardware pipeline. Every value is an integer in the
format the hardware register uses (Q10 gains, Q8 weights, ...)."""
import csv
from dataclasses import dataclass, field, fields


def _m(rows):
    return [list(r) for r in rows]


@dataclass
class HwConfig:
    width: int = 1920
    height: int = 1080
    bayer_pattern: str = "rggb"

    # DPC
    dpc_thres: int = 30
    dpc_mode: str = "gradient"
    # BLC: black level per channel (r, gr, gb, b), 10-bit codes
    bl: list = field(default_factory=lambda: [0, 0, 0, 0])
    bl_alpha: int = 0
    bl_beta: int = 0
    # AWB gains Q10 (r, gr, gb, b)
    awb_gain: list = field(default_factory=lambda: [1536, 1024, 1024, 1126])
    # CNF
    cnf_thres: int = 0
    # CCM Q10, 3x4 (last column = offset in Q10 units)
    ccm: list = field(default_factory=lambda: _m([[1024, 0, 0, 0], [0, 1024, 0, 0], [0, 0, 1024, 0]]))
    # Gamma exponent used to build the 10->8 bit LUT
    gamma: float = 0.45
    # CSC Q10 (BT.601 limited range)
    csc: list = field(default_factory=lambda: _m([[263, 516, 100, 16384],
                                                  [-152, -298, 450, 131072],
                                                  [450, -377, -73, 131072]]))
    # NLM
    nlm_h: int = 15
    nlm_search_r: int = 2
    nlm_dshift: int = 3
    # BNF
    bnf_dw: list = field(default_factory=lambda: _m([[8, 12, 32, 12, 8],
                                                     [12, 64, 128, 64, 12],
                                                     [32, 128, 1024, 128, 32],
                                                     [12, 64, 128, 64, 12],
                                                     [8, 12, 32, 12, 8]]))
    bnf_rw: list = field(default_factory=lambda: [0, 8, 16, 32])
    bnf_rthres: list = field(default_factory=lambda: [128, 32, 8])
    # EE
    edge_filter: list = field(default_factory=lambda: _m([[-1, 0, -1, 0, -1],
                                                          [-1, 0, 8, 0, -1],
                                                          [-1, 0, -1, 0, -1]]))
    ee_gain: list = field(default_factory=lambda: [32, 128])
    ee_thres: list = field(default_factory=lambda: [32, 64])
    ee_emclip: list = field(default_factory=lambda: [-64, 64])
    # FCS
    fcs_edge: list = field(default_factory=lambda: [32, 64])
    fcs_gain: int = 256
    # HSC
    hue: int = 0
    saturation: int = 256
    # BCC
    brightness: int = 0
    contrast: int = 0
    # 3A statistics (0 = automatic centred 16x16 grid); all even
    stats_x0: int = 0
    stats_y0: int = 0
    stats_zw: int = 0
    stats_zh: int = 0
    awb_sat: int = 1020          # quads with any channel >= this are skipped
    awb_dark: int = 16           # quads with any channel < this are skipped
    af_coring: int = 4

    # ------------------------------------------------------------------ io
    @classmethod
    def from_csv(cls, path):
        """CSV rows: name,value[,description]. Lists/matrices are written as
        space separated values, matrix rows separated by ';'."""
        cfg = cls()
        types = {f.name: f for f in fields(cls)}
        with open(path, encoding="utf-8-sig") as fh:
            for row in csv.reader(fh):
                if not row or row[0].startswith("#") or row[0] == "Variables":
                    continue
                name, val = row[0].strip(), row[1].strip()
                if name not in types:
                    raise KeyError(f"unknown parameter {name!r} in {path}")
                setattr(cfg, name, _parse(val, getattr(cfg, name)))
        return cfg

    def to_csv(self, path):
        with open(path, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["Variables", "Values"])
            for f in fields(self):
                w.writerow([f.name, _fmt(getattr(self, f.name))])


def _parse(text, like):
    if isinstance(like, list):
        if ";" in text:
            return [[int(v) for v in r.split()] for r in text.split(";")]
        return [int(v) for v in text.split()]
    if isinstance(like, float):
        return float(text)
    if isinstance(like, int):
        return int(text)
    return text


def _fmt(v):
    if isinstance(v, list) and v and isinstance(v[0], list):
        return ";".join(" ".join(str(x) for x in r) for r in v)
    if isinstance(v, list):
        return " ".join(str(x) for x in v)
    return str(v)
