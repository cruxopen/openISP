# SPDX-License-Identifier: MIT
"""Firmware (sw/) on the host, against the Python reference:
  * tuning file -> register words + tables, vs hwmodel.regmap (bit exact)
  * 3A daemon (openisp run, simulation backend) vs hwmodel.aaa, frame by frame
  * closed loop: Python renders every frame with the exposure chosen by the
    daemon; AE reaches its target and AWB removes the colour cast."""
import dataclasses
import json
import math
import os
import shutil
import subprocess
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hwmodel import aaa, regmap                          # noqa: E402
from hwmodel import stats as s3a                         # noqa: E402
from hwmodel.blocks import gamma_lut, nlm_lut            # noqa: E402
from hwmodel.config import HwConfig                      # noqa: E402

pytestmark = pytest.mark.skipif(not shutil.which("gcc") or not shutil.which("make"), reason="no gcc/make")


@pytest.fixture(scope="module")
def fw(tmp_path_factory):
    build = str(tmp_path_factory.mktemp("fwbuild"))
    p = subprocess.run(["make", "-C", os.path.join(ROOT, "sw"), f"BUILD={build}", "CFLAGS=-O2 -Werror"],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert p.returncode == 0, p.stdout
    return os.path.join(build, "openisp")


def isp_section(c: HwConfig):
    d = dataclasses.asdict(c)
    return d


def write_tuning(path, cfg, ae=None, awb=None, tables=None):
    t = {"version": 1, "isp": isp_section(cfg)}
    if tables:
        t["isp"].update(tables)
    if ae is not None:
        t["ae"] = ae
    if awb is not None:
        t["awb"] = awb
    with open(path, "w") as f:
        json.dump(t, f, indent=1)


def rand_cfg(rng):
    w = int(rng.integers(32, 1921)) & ~1
    h = int(rng.integers(32, 1081)) & ~1
    c = HwConfig(width=w, height=h, bayer_pattern=str(rng.choice(["rggb", "bggr", "grbg", "gbrg"])))
    c.dpc_thres = int(rng.integers(0, 1024)); c.dpc_mode = str(rng.choice(["gradient", "mean"]))
    c.bl = [int(v) for v in rng.integers(0, 1024, 4)]
    c.bl_alpha, c.bl_beta = int(rng.integers(-512, 512)), int(rng.integers(-512, 512))
    c.awb_gain = [int(v) for v in rng.integers(0, 16384, 4)]
    c.cnf_thres = int(rng.integers(0, 1024))
    c.ccm = [[int(v) for v in rng.integers(-32768, 32768, 3)] + [int(rng.integers(-2 ** 21, 2 ** 21))] for _ in range(3)]
    c.csc = [[int(v) for v in rng.integers(-32768, 32768, 3)] + [int(rng.integers(-2 ** 21, 2 ** 21))] for _ in range(3)]
    c.gamma = float(rng.uniform(0.3, 1.0))
    c.nlm_h = int(rng.integers(2, 60)); c.nlm_dshift = int(rng.integers(0, 8))
    c.bnf_dw = [[int(v) for v in rng.integers(0, 4096, 5)] for _ in range(5)]
    c.bnf_rw = [int(v) for v in rng.integers(0, 256, 4)]
    c.bnf_rthres = [int(v) for v in rng.integers(0, 256, 3)]
    c.edge_filter = [[int(v) for v in rng.integers(-128, 128, 5)] for _ in range(3)]
    c.ee_gain = [int(v) for v in rng.integers(0, 1024, 2)]
    c.ee_thres = [int(v) for v in rng.integers(0, 65536, 2)]
    c.ee_emclip = [int(v) for v in rng.integers(-512, 512, 2)]
    c.fcs_edge = [int(v) for v in rng.integers(0, 2048, 2)]
    c.fcs_gain = int(rng.integers(0, 512))
    c.hue = int(rng.integers(-720, 720)); c.saturation = int(rng.integers(0, 1024))
    c.brightness = int(rng.integers(-256, 256)); c.contrast = int(rng.integers(-128, 128))
    if rng.random() < 0.5:
        c.stats_zw = 2 * int(rng.integers(1, w // 32 + 1)); c.stats_zh = 2 * int(rng.integers(1, h // 32 + 1))
        c.stats_x0 = 2 * int(rng.integers(0, (w - 16 * c.stats_zw) // 2 + 1))
        c.stats_y0 = 2 * int(rng.integers(0, (h - 16 * c.stats_zh) // 2 + 1))
    c.awb_sat, c.awb_dark = int(rng.integers(0, 1024)), int(rng.integers(0, 1024))
    c.af_coring = int(rng.integers(0, 512))
    return c


def pack(fw, path):
    p = subprocess.run([fw, "pack", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return p.returncode, [tuple(int(x, 16) for x in ln.split()[1:]) for ln in p.stdout.splitlines()], p.stderr


def expected_writes(c, tables=None):
    words = regmap.reg_words(c)
    w = [(a, words.get(a, 0)) for a in range(regmap.NREG)]
    g = tables["gamma_lut"] if tables else gamma_lut(c.gamma)
    n = tables["nlm_lut"] if tables else nlm_lut(c.nlm_h, c.nlm_dshift)
    w += [(regmap.GAMMA_BASE + a, int(v)) for a, v in enumerate(g)]
    w += [(regmap.NLM_LUT_BASE + a, int(v)) for a, v in enumerate(n)]
    return w


@pytest.mark.parametrize("seed", range(6))
def test_pack_matches_regmap(fw, tmp_path, seed):
    rng = np.random.default_rng(seed)
    c = rand_cfg(rng) if seed else HwConfig()
    tables = None
    if seed % 3 == 2:     # explicit tables override gamma / nlm_h
        tables = {"gamma_lut": [int(v) for v in rng.integers(0, 256, 1024)],
                  "nlm_lut": [int(v) for v in rng.integers(0, 256, 256)]}
    path = str(tmp_path / "t.json")
    write_tuning(path, c, tables=tables)
    rc, got, err = pack(fw, path)
    assert rc == 0, err
    assert got == expected_writes(c, tables)


def test_pack_rejects_bad_values(fw, tmp_path):
    path = str(tmp_path / "t.json")
    for bad in ({"dpc_thres": 1024}, {"ccm": [[40000, 0, 0, 0], [0, 1024, 0, 0], [0, 0, 1024, 0]]},
                {"bayer_pattern": "rgbg"}, {"no_such_field": 1}, {"stats_zw": 3}):
        c = dataclasses.asdict(HwConfig())
        c.update(bad)
        with open(path, "w") as f:
            json.dump({"isp": c}, f)
        rc, _, err = pack(fw, path)
        assert rc != 0 and err, bad


# ------------------------------------------------------------------ 3A
ILLUMS = [
    {"name": "A", "cct": 2856, "gains": [1100, 2900],
     "ccm": [[1600, -400, -176, 0], [-300, 1500, -176, 0], [-100, -700, 1824, 0]]},
    {"name": "TL84", "cct": 4000, "gains": [1500, 2000],
     "ccm": [[1700, -500, -176, 0], [-250, 1450, -176, 0], [-60, -500, 1584, 0]]},
    {"name": "D65", "cct": 6504, "gains": [1950, 1400],
     "ccm": [[1800, -600, -176, 0], [-200, 1400, -176, 0], [-30, -400, 1454, 0]]},
]
AE = dict(aaa.AE_DEFAULTS, lines_max=1000, flicker_lines=0, delay=2)
# KV260 AR1335 driver: "gain" is an index, 2x per step (measured on the board)
AE_TABLE = dict(AE, gain_table=[1.0, 2.0, 4.0, 8.0, 16.0, 32.0], gain_code_min=0, gain_code_max=5)
AWB = dict(aaa.AWB_DEFAULTS, illuminants=ILLUMS)


def stats_bytes(st, n):
    a = [np.array([n, 0], "<u4").tobytes(), np.zeros(1, "<u8").tobytes()]
    for k in ("awb_r", "awb_g", "awb_b", "awb_n", "ae_sum", "ae_hist", "af"):
        v = np.asarray(st[k] if k in st else np.zeros(256, np.int64)).ravel()
        assert v.max() < 2 ** 32
        a.append(v.astype("<u4").tobytes())
    return b"".join(a)


def put_stats(d, n, st):
    tmp = os.path.join(d, f".tmp{n}")
    with open(tmp, "wb") as f:
        f.write(stats_bytes(st, n))
    os.replace(tmp, os.path.join(d, f"stats_{n}.bin"))


def scene(h, w, rng):
    """Linear RGB radiance in [0, 1], smooth patches."""
    base = rng.uniform(0.05, 1.0, (h // 16 + 1, w // 16 + 1, 3))
    return np.kron(base, np.ones((16, 16, 1)))[:h, :w]


def render(sc, exposure, cast, pattern="rggb", noise=None):
    """Bayer RAW10 of the scene: camera = scene x cast x exposure / 1000."""
    h, w, _ = sc.shape
    cam = sc * np.asarray(cast) * (exposure / 1000.0) * 400.0
    ch = {"rggb": ((0, 1), (1, 2))}[pattern]
    raw = np.zeros((h, w))
    for y in (0, 1):
        for x in (0, 1):
            raw[y::2, x::2] = cam[y::2, x::2, ch[y][x]]
    if noise is not None:
        raw += noise.normal(0, 1.0, raw.shape)
    return np.clip(np.floor(raw + 0.5), 0, 1023).astype(np.int64)


def parse_f(line):
    t = line.split()
    i = t.index("ccm")
    return dict(frame=int(t[1]), mean=float(t[3]), lines=int(t[5]), code=int(t[7]), dgain=float(t[9]),
                wb=[int(v) for v in t[11:15]], ccm=[int(v) for v in t[i + 1:i + 13]],
                cct=float(t[i + 14]))


class Ref:
    """Python model of the daemon loop in sw/app/openisp.c (frame())."""

    def __init__(self, grid, ae, awb):
        self.grid, self.ae, self.awb = grid, ae, awb
        lines, code, again, dg = aaa.ae_split(0.5 * (ae["lines_min"] + ae["lines_max"]), ae)
        self.e, self.dg, self.lines, self.code, self.mean = lines * again * dg, dg, lines, code, 0.0
        self.last = 0
        self.st = aaa.AwbFwState()
        self.gr, self.gb, self.ccm, self.cct = 1536, 1126, [[1024, 0, 0, 0], [0, 1024, 0, 0], [0, 0, 1024, 0]], 0.0

    def step(self, n, st):
        r = aaa.awb_step(self.st, st, self.awb)
        if r:
            self.gr, self.gb, ccm, self.cct = r
            self.ccm = ccm or self.ccm
        if n >= self.last + self.ae["delay"] + 1:
            e = aaa.ae_target_exposure(st, self.grid, self.ae, self.e, self.dg)
            self.mean = aaa.ae_measure(st, self.grid) * self.dg
            self.lines, self.code, again, dg = aaa.ae_split(e, self.ae)
            self.e, self.dg, self.last = self.lines * again * dg, dg, n
        return dict(frame=n, mean=self.mean, lines=self.lines, code=self.code, dgain=self.dg,
                    wb=aaa.wb_registers(self.gr, self.gb, self.dg), ccm=sum(self.ccm, []), cct=self.cct)


@pytest.mark.parametrize("ae", [AE, AE_TABLE], ids=["linear_gain", "gain_table"])
def test_daemon_matches_reference(fw, tmp_path, ae):
    """Pre-recorded statistics (random exposure / cast per frame): every
    frame's AE, AWB, CCM and register writes equal the Python reference."""
    AE = ae
    rng = np.random.default_rng(3)
    h, w = 192, 256
    cfg = HwConfig(width=w, height=h)
    grid = s3a.zone_grid(w, h)
    d = str(tmp_path / "st")
    os.makedirs(d)
    nfr = 14
    stats = []
    sc = scene(h, w, rng)
    for n in range(1, nfr + 1):
        cast = [rng.uniform(0.4, 1.6), 1.0, rng.uniform(0.4, 1.6)]
        raw = render(sc, rng.uniform(100, 5000), cast, noise=rng)
        st = s3a.bayer_stats(raw, "rggb", grid, cfg.awb_sat, cfg.awb_dark)
        st["af"] = rng.integers(0, 10 ** 6, (16, 16))
        stats.append(st)
        put_stats(d, n, st)
    open(os.path.join(d, "end"), "w").close()
    tun = str(tmp_path / "t.json")
    write_tuning(tun, cfg, ae=AE, awb=AWB)
    log = str(tmp_path / "w.log")
    p = subprocess.run([fw, "-d", f"sim:{log}:{d}:{w}x{h}", "run", tun, "-v"],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    got = [parse_f(ln) for ln in p.stdout.splitlines() if ln.startswith("F ")]
    assert len(got) == nfr
    ref = Ref(grid, AE, AWB)
    for n, (g, st) in enumerate(zip(got, stats), 1):
        e = ref.step(n, st)
        for k in ("frame", "lines", "code", "wb", "ccm"):
            assert g[k] == e[k], (n, k, g[k], e[k])
        for k in ("mean", "dgain", "cct"):
            assert math.isclose(g[k], e[k], rel_tol=1e-6, abs_tol=1e-3), (n, k, g[k], e[k])
    # the last committed register set = regmap of the final configuration
    blocks, cur = [], []
    for ln in open(log):
        if ln.startswith("C"):
            blocks.append(cur)
            cur = []
        else:
            _, a, v = ln.split()
            cur.append((int(a, 16), int(v, 16)))
    assert len(blocks) == nfr + 1                        # initial apply + one per frame
    last = dict(blocks[-1])
    cfg.awb_gain = got[-1]["wb"]
    cfg.ccm = [got[-1]["ccm"][4 * r:4 * r + 4] for r in range(3)]
    want = regmap.reg_words(cfg)
    assert all(last[a] == want.get(a, 0) for a in range(regmap.NREG))
    assert len(blocks[0]) == regmap.NREG + 1024 + 256     # first apply includes the tables


def test_closed_loop_ae_awb(fw, tmp_path):
    """Python is the sensor: frame n is rendered with the exposure the daemon
    chose at least `delay` frames earlier.  AE converges to the target and AWB
    to the inverse of the colour cast."""
    rng = np.random.default_rng(5)
    h, w = 192, 256
    cfg = HwConfig(width=w, height=h)
    grid = s3a.zone_grid(w, h)
    sc = scene(h, w, rng) * 0.08             # dark: needs the full integration time + analog gain
    cast = [0.62, 1.0, 1.45]                     # warm-ish light: R high gain needed low
    ae = dict(AE, lines_max=4000, gain_code_max=64)
    d = str(tmp_path / "st")
    os.makedirs(d)
    tun = str(tmp_path / "t.json")
    write_tuning(tun, cfg, ae=ae, awb=dict(aaa.AWB_DEFAULTS, speed=0.6))
    log = str(tmp_path / "w.log")
    proc = subprocess.Popen([fw, "-d", f"sim:{log}:{d}:{w}x{h}", "run", tun, "-v"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    settings = {0: aaa.ae_split(0.5 * (ae["lines_min"] + ae["lines_max"]), ae)}
    hist = []
    try:
        for n in range(1, 31):
            k = max(f for f in settings if f <= n - ae["delay"]) if n > ae["delay"] else 0
            lines, code, again, _ = settings[k]
            raw = render(sc, lines * again, cast, noise=rng)
            put_stats(d, n, s3a.bayer_stats(raw, "rggb", grid, cfg.awb_sat, cfg.awb_dark))
            f = parse_f(proc.stdout.readline())
            assert f["frame"] == n
            settings[n] = (f["lines"], f["code"], f["code"] / ae["gain_1x"], f["dgain"])
            hist.append(f)
    finally:
        open(os.path.join(d, "end"), "w").close()
        proc.wait(timeout=10)
    assert proc.returncode == 0, proc.stderr.read()
    last = hist[-1]
    # AE: the measured (pre-gain) mean x digital gain is at the target
    lines, code, again, dg = settings[30]
    raw = render(sc, lines * again, cast)
    m = aaa.ae_measure(s3a.bayer_stats(raw, "rggb", grid, 1020, 16), grid) * dg
    assert abs(m / ae["target"] - 1) < 0.05, (m, last)
    # AWB: gains x cast = neutral (gray world of a random scene: within 10 %)
    sr = sc[..., 0].mean() * cast[0] / (sc[..., 1].mean())
    sb = sc[..., 2].mean() * cast[2] / (sc[..., 1].mean())
    assert abs(last["wb"][0] / last["wb"][1] * sr - 1) < 0.1, last
    assert abs(last["wb"][3] / last["wb"][1] * sb - 1) < 0.1, last


def test_gamma_nlm_tables_match_python(fw, tmp_path):
    """Without explicit tables the firmware computes them like hwmodel."""
    path = str(tmp_path / "t.json")
    for g, nh, ds in [(0.45, 15, 3), (0.3333, 7, 2), (0.7, 41, 5), (1.0, 2, 0), (0.41666, 25, 4)]:
        c = HwConfig(gamma=g, nlm_h=nh, nlm_dshift=ds)
        write_tuning(path, c)
        rc, got, err = pack(fw, path)
        assert rc == 0, err
        assert got == expected_writes(c), (g, nh, ds)


def test_closed_loop_digital_gain_only(fw, tmp_path):
    """--dgain-only (no sensor exposure control): the exposure stays fixed and
    AE reaches the target through the digital gain in the WB registers."""
    rng = np.random.default_rng(9)
    h, w = 192, 256
    cfg = HwConfig(width=w, height=h)
    grid = s3a.zone_grid(w, h)
    sc = scene(h, w, rng) * 0.25
    d = str(tmp_path / "st")
    os.makedirs(d)
    tun = str(tmp_path / "t.json")
    write_tuning(tun, cfg, ae=dict(AE, dgain_max=8.0), awb=dict(aaa.AWB_DEFAULTS, enable=False))
    proc = subprocess.Popen([fw, "-d", f"sim:{tmp_path / 'w.log'}:{d}:{w}x{h}", "run", tun, "-v", "--dgain-only"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    raw = render(sc, 500.0, [1, 1, 1])                   # fixed exposure
    st = s3a.bayer_stats(raw, "rggb", grid, cfg.awb_sat, cfg.awb_dark)
    try:
        for n in range(1, 25):
            put_stats(d, n, st)
            f = parse_f(proc.stdout.readline())
    finally:
        open(os.path.join(d, "end"), "w").close()
        proc.wait(timeout=10)
    assert proc.returncode == 0
    lines0 = aaa.ae_split(0.5 * (AE["lines_min"] + AE["lines_max"]), AE)[0]
    assert f["lines"] == lines0 and f["code"] == AE["gain_1x"]
    m = aaa.ae_measure(st, grid)
    assert abs(m * f["dgain"] / AE["target"] - 1) < 0.03, (m, f)
    assert f["wb"][1] == int(math.floor(1024 * f["dgain"] + 0.5))
