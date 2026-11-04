# SPDX-License-Identifier: MIT
"""PC tuning tool (isptune) on a synthetic sensor with a known colour
response, black level and noise; the exported file is loaded by the
firmware; live tuning against the real 3A daemon (simulation backend)."""
import json
import os
import subprocess
import sys
import time

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hwmodel import regmap                               # noqa: E402
from hwmodel.common import cfa_map                       # noqa: E402
from isptune import chart, tuning as T                   # noqa: E402
from isptune.__main__ import main                        # noqa: E402
from isptune.live import Live                            # noqa: E402

from test_fw import expected_writes, fw, pack            # noqa: E402,F401

W, H, BL = 400, 280, [64, 66, 65, 63]
M_TRUE = np.array([[1.62, -0.42, -0.20], [-0.28, 1.47, -0.19], [-0.08, -0.55, 1.63]])   # rows sum to 1
CAST = np.array([0.55, 1.0, 1.6])          # tungsten-like: weak R gain needed, strong B
CORNERS = [(40, 30), (360, 30), (360, 250), (40, 250)]


def write_raw(path, raw):
    raw.astype("<u2").tofile(path)


def render_chart(rng, scale=700.0, noise=1.5):
    """Bayer RAW10 of a ColorChecker: camera = cast * M^-1 * linear sRGB."""
    tgt = chart.srgb_to_linear(chart.CHECKER_SRGB)
    cam = (tgt @ np.linalg.inv(M_TRUE).T) * CAST * scale
    img = np.full((H, W, 3), 0.18) * CAST * scale
    cen, pitch = chart.patch_centres(CORNERS)
    hp = int(pitch * 0.42)
    for (cx, cy), c in zip(cen, cam):
        img[int(cy) - hp:int(cy) + hp, int(cx) - hp:int(cx) + hp] = c
    ch = cfa_map("grbg", H, W)
    plane = {0: 0, 1: 1, 2: 1, 3: 2}
    raw = np.zeros((H, W))
    for c in range(4):
        raw[ch == c] = img[..., plane[c]][ch == c] + BL[c]
    raw += rng.normal(0, noise, raw.shape)
    return np.clip(np.floor(raw + 0.5), 0, 1023).astype(np.int64)


@pytest.fixture(scope="module")
def calibrated(tmp_path_factory):
    d = tmp_path_factory.mktemp("tune")
    rng = np.random.default_rng(1)
    tun = str(d / "t.json")
    main(["init", "-o", tun, "--width", str(W), "--height", str(H), "--pattern", "grbg", "--sensor", "ar1335"])
    dark = np.array(BL)[cfa_map("grbg", H, W)] + rng.normal(0, 1.2, (H, W))
    dark[rng.integers(0, H, 30), rng.integers(0, W, 30)] = 900          # hot pixels
    write_raw(str(d / "dark.raw"), np.clip(np.floor(dark + 0.5), 0, 1023))
    main(["blc", str(d / "dark.raw"), "-t", tun])
    write_raw(str(d / "chart.raw"), render_chart(rng))
    cs = ",".join(f"{x},{y}" for x, y in CORNERS)
    main(["chart", str(d / "chart.raw"), "-t", tun, "--corners", cs, "--illuminant", "A", "--set-static"])
    main(["noise", str(d / "chart.raw"), "-t", tun, "--corners", cs])
    return d, tun


def test_black_level(calibrated):
    _, tun = calibrated
    t = T.load(tun)
    assert t["isp"]["bl"] == BL
    assert t["meta"]["blc"]["hot_pixels"] >= 25


def test_chart_white_balance_and_ccm(calibrated):
    _, tun = calibrated
    t = T.load(tun)
    (ill,) = t["awb"]["illuminants"]
    gr, gb = ill["gains"]
    assert abs(gr / (1024 / CAST[0]) - 1) < 0.01 and abs(gb / (1024 / CAST[2]) - 1) < 0.01, ill["gains"]
    ccm = np.asarray(ill["ccm"])
    assert (ccm[:, :3].sum(1) == 1024).all() and (ccm[:, 3] == 0).all()
    assert np.abs(ccm[:, :3] / 1024 - M_TRUE).max() < 0.03, ccm
    rep = t["meta"]["chart"]["A"]
    assert rep["de2000_ccm"]["mean"] < 1.0 < rep["de2000_wb_only"]["mean"], rep
    assert t["isp"]["ccm"] == ill["ccm"]


def test_noise_suggestion(calibrated):
    _, tun = calibrated
    t = T.load(tun)
    n = t["meta"]["noise"]
    assert 0.2 < n["sigma_y"] < 10 and 2 <= t["isp"]["nlm_h"] <= 60
    assert t["isp"]["bnf_rthres"] == sorted(t["isp"]["bnf_rthres"], reverse=True)


def test_preview_and_export_loaded_by_firmware(calibrated, fw):
    d, tun = calibrated
    png = str(d / "chart.png")
    main(["preview", str(d / "chart.raw"), "-t", tun, "-o", png, "--auto"])
    with open(png, "rb") as f:
        head = f.read(24)
    assert head[:8] == b"\x89PNG\r\n\x1a\n" and int.from_bytes(head[16:20], "big") == W
    board = str(d / "board.json")
    main(["export", "-t", tun, "-o", board, "--gamma-curve", "srgb"])
    t = T.load(board)
    assert t["isp"]["gamma_lut"] == [int(v) for v in T.srgb_gamma_lut()]
    rc, got, err = pack(fw, board)
    assert rc == 0, err
    cfg, g, n = T.hwconfig(t)
    assert got == expected_writes(cfg, {"gamma_lut": g, "nlm_lut": n})
    main(["check", "-t", board])


def test_preview_neutral_gray(calibrated):
    """After calibration the neutral patches come out neutral (U, V ~ 128)."""
    from isptune.preview import process
    d, tun = calibrated
    raw = np.fromfile(str(d / "chart.raw"), "<u2").reshape(H, W).astype(np.int64)
    _, yuv, _, _ = process(raw, T.load(tun))
    cen, _ = chart.patch_centres(CORNERS)
    for i in chart.NEUTRALS[1:5]:
        cx, cy = (int(v) for v in cen[i])
        uv = yuv[cy - 4:cy + 4, cx - 4:cx + 4, 1:].reshape(-1, 2).mean(0)
        assert np.abs(uv - 128).max() < 3, (i, uv)


def test_live_tuning_with_daemon(calibrated, fw, tmp_path):
    _, tun = calibrated
    board = str(tmp_path / "board.json")
    main(["export", "-t", tun, "-o", board])
    st = tmp_path / "st"
    st.mkdir()
    log = str(tmp_path / "w.log")
    sock = str(tmp_path / "live.sock")
    proc = subprocess.Popen([fw, "-d", f"sim:{log}:{st}:{W}x{H}", "run", board, "--listen", f"unix:{sock}"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        for _ in range(100):
            try:
                lv = Live(f"unix:{sock}")
                break
            except OSError:
                time.sleep(0.05)
        else:
            raise AssertionError(f"daemon did not listen: rc={proc.poll()} {proc.stderr.read() if proc.poll() is not None else ''}")
        assert lv.get("saturation") == "256"
        lv.set("saturation", 300)
        lv.set("ccm", [[1100, -50, -26, 0], [0, 1024, 0, 0], [0, 0, 1024, 0]])
        assert lv.get("saturation") == "300"
        with pytest.raises(RuntimeError):
            lv.set("saturation", "1 2")                  # wrong number of values
        with pytest.raises(RuntimeError):
            lv.set("stats_zw", 1000)                     # grid larger than the frame
        assert lv.get("stats_zw") == "0"
        s = lv.state()
        assert s["ae"]["enable"] == 1 and "awb" in s
        dump = lv.dump()
        assert dump["width"] == W and dump["bayer_pattern"] == "grbg" and dump["saturation"] == 300
        t = T.load(board)
        t["isp"]["brightness"] = 7
        t["isp"]["width"] = 1920                         # ignored: the format comes from the driver
        lv.push(t)
        assert lv.get("brightness") == "7" and lv.get("width") == str(W)
        assert lv.get("saturation") == "256"
        time.sleep(0.3)                                  # no frames: applied on the idle timeout
        lv.close()
    finally:
        open(st / "end", "w").close()
        proc.wait(timeout=10)
    assert proc.returncode == 0, proc.stderr.read()
    blocks, cur = [], []
    for ln in open(log):
        if ln.startswith("C"):
            blocks.append(dict(cur))
            cur = []
        else:
            cur.append(tuple(int(x, 16) for x in ln.split()[1:]))
    sat_word = [f for f in regmap.FIELDS if f[0] == "hsc_sat"][0][1]
    bright_word = [f for f in regmap.FIELDS if f[0] == "bright"][0][1]
    assert any(b.get(sat_word, 0) & 0x3ff == 300 for b in blocks)
    assert blocks[-1][bright_word] & 0x1ff == 7 and blocks[-1][sat_word] & 0x3ff == 256
    assert len(blocks[-1]) == regmap.NREG + 1024 + 256       # push reloads the tables


def test_ar1335_profile_loads_in_firmware(fw, tmp_path):
    """isptune init --sensor ar1335: measured control names / gain table, and
    the firmware accepts the file (sensor and gain_table keys)."""
    tun, board = str(tmp_path / "t.json"), str(tmp_path / "b.json")
    main(["init", "-o", tun, "--width", "3840", "--height", "2160", "--sensor", "ar1335"])
    t = T.load(tun)
    assert t["sensor"] == {"name": "ar1335", "exposure_ctrl": "coarse_integration_time", "gain_ctrl": "gain"}
    assert t["ae"]["gain_table"] == [1, 2, 4, 8, 16, 32] and t["ae"]["lines_max"] == 3150 - 32
    assert (t["ae"]["gain_code_min"], t["ae"]["gain_code_max"]) == (0, 4)
    assert t["isp"]["bayer_pattern"] == "rggb"
    main(["export", "-t", tun, "-o", board])
    rc, got, err = pack(fw, board)
    assert rc == 0, err
    t["ae"]["gain_table"] = [2, 1]
    T.save(t, board)
    with pytest.raises(ValueError):
        T.check(t)
    rc, _, err = pack(fw, board)
    assert rc != 0 and "gain_table" in err


def test_no_chart_calibration(fw, tmp_path):
    """Without a chart: white-target WB per illuminant and the noise model
    from flat-field pairs (known shot-noise gain / read noise, vignetting)."""
    rng = np.random.default_rng(5)
    tun = str(tmp_path / "t.json")
    main(["init", "-o", tun, "--width", str(W), "--height", str(H), "--pattern", "rggb"])
    t = T.load(tun)
    t["isp"]["bl"] = BL
    T.save(t, tun)
    ch = cfa_map("rggb", H, W)
    yy, xx = np.mgrid[0:H, 0:W]
    vig = 1 - 0.35 * (((yy - H / 2) / H) ** 2 + ((xx - W / 2) / W) ** 2) * 4    # lens fall-off
    K, RN = 0.8, 2.0                                     # DN per electron, read noise (DN)

    def frames(level, cast, n=2):
        out = []
        for _ in range(n):
            mean = level * vig * np.asarray(cast)[[0, 1, 1, 2]][ch]
            e = rng.poisson(mean / K) * K + rng.normal(0, RN, (H, W))
            out.append(np.clip(np.floor(e + np.asarray(BL)[ch] + 0.5), 0, 1023))
        return np.concatenate(out).astype("<u2")

    for name, cct, cast in (("cool", 5000, (0.8, 1.0, 0.9)), ("warm", 3000, (1.25, 1.0, 0.55))):
        p = str(tmp_path / f"{name}.raw")
        frames(500, cast).tofile(p)
        main(["wb", p, "-t", tun, "--illuminant", name, "--cct", str(cct)])
    ills = T.load(tun)["awb"]["illuminants"]
    assert [i["name"] for i in ills] == ["warm", "cool"]
    for i, cast in zip(ills, ((1.25, 1.0, 0.55), (0.8, 1.0, 0.9))):
        assert abs(i["gains"][0] / (1024 / cast[0]) - 1) < 0.01 and abs(i["gains"][1] / (1024 / cast[2]) - 1) < 0.01
        assert i["ccm_source"] == "generic"
    flats = []
    for lvl in (60, 150, 300, 600, 1100):               # the last one clips: skipped
        p = str(tmp_path / f"flat{lvl}.raw")
        frames(lvl, (1, 1, 1)).tofile(p)
        flats.append(p)
    main(["flat", *flats, "-t", tun])
    n = T.load(tun)["meta"]["noise"]
    assert abs(n["shot_gain"] / K - 1) < 0.08 and abs(np.sqrt(n["read_var"]) / RN - 1) < 0.25, n
    assert n["points"] == 16 and 2 <= n["nlm_h"] <= 60
    board = str(tmp_path / "b.json")
    main(["export", "-t", tun, "-o", board])
    rc, _, err = pack(fw, board)
    assert rc == 0, err
