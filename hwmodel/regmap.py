# SPDX-License-Identifier: MIT
"""Register map of the hardware ISP (single source of truth).

Word addresses (the AXI4-Lite byte address is 4x the word address):
  0x0000 - 0x003F  configuration registers (FIELDS below), double buffered:
                   writes go to the pending bank, CTRL.COMMIT copies it to the
                   active bank between two frames (see CTRL_* below)
  0x0040 - 0x004F  control / status / interrupt block (CTRL_REGS)
  0x0400 - 0x07FF  gamma LUT, 1024 x 8 bit          (write only)
  0x0800 - 0x08FF  NLM weight LUT, 256 x 8 bit       (write only)
  0x1000 - 0x1600  AWB/AE statistics (stats_bayer read map)
  0x2000 - 0x2100  AF statistics (stats_af read map)
Generated from this file (python -m hwmodel.regmap):
  hw/rtl/top/isp_regs.sv        RTL register file
  sw/include/openisp_regs.h     C header for the driver and the firmware
"""
from . import blocks as b
from .common import _PATTERNS
from .config import HwConfig
from .stats import zone_grid

GAMMA_BASE, NLM_LUT_BASE = 0x400, 0x800
STATS_BAYER_BASE, STATS_AF_BASE = 0x1000, 0x2000

# (name, word, lsb, width, signed)
FIELDS = [
    ("width", 0, 0, 13, 0), ("height", 0, 16, 13, 0),
    ("cfa", 1, 0, 8, 0),
    ("dpc_thres", 2, 0, 10, 0), ("dpc_mean", 2, 16, 1, 0),
    ("bl_r", 3, 0, 10, 0), ("bl_gr", 3, 16, 10, 0), ("bl_gb", 4, 0, 10, 0), ("bl_b", 4, 16, 10, 0),
    ("bl_alpha", 5, 0, 10, 1), ("bl_beta", 5, 16, 10, 1),
    ("awb_r", 6, 0, 14, 0), ("awb_gr", 6, 16, 14, 0), ("awb_gb", 7, 0, 14, 0), ("awb_b", 7, 16, 14, 0),
    ("cnf_thres", 8, 0, 10, 0),
]
FIELDS += [(f"ccm_m{k}", 9 + k // 2, 16 * (k % 2), 16, 1) for k in range(9)]
FIELDS += [(f"ccm_o{k}", 14 + k, 0, 22, 1) for k in range(3)]
FIELDS += [(f"csc_m{k}", 17 + k // 2, 16 * (k % 2), 16, 1) for k in range(9)]
FIELDS += [(f"csc_o{k}", 22 + k, 0, 22, 1) for k in range(3)]
FIELDS += [("nlm_dshift", 25, 0, 3, 0)]
FIELDS += [(f"bnf_dw{t}", 26 + t // 2, 16 * (t % 2), 12, 0) for t in range(25)]
FIELDS += [(f"bnf_rw{k}", 39, 8 * k, 8, 0) for k in range(4)]
FIELDS += [(f"bnf_t{k}", 40, 8 * k, 8, 0) for k in range(3)]
FIELDS += [(f"ee_f{t}", 41 + t // 4, 8 * (t % 4), 8, 1) for t in range(15)]
FIELDS += [
    ("ee_g0", 45, 0, 10, 0), ("ee_g1", 45, 16, 10, 0),
    ("ee_t0", 46, 0, 16, 0), ("ee_t1", 46, 16, 16, 0),
    ("ee_clo", 47, 0, 10, 1), ("ee_chi", 47, 16, 10, 1),
    ("fcs_e0", 48, 0, 11, 0), ("fcs_e1", 48, 16, 11, 0),
    ("fcs_gain", 49, 0, 9, 0),
    ("hsc_sin", 50, 0, 10, 1), ("hsc_cos", 50, 16, 10, 1),
    ("hsc_sat", 51, 0, 10, 0),
    ("bright", 52, 0, 9, 1), ("contrast", 52, 16, 8, 1),
    ("st_x0", 53, 0, 13, 0), ("st_y0", 53, 16, 13, 0),
    ("st_zw", 54, 0, 13, 0), ("st_zh", 54, 16, 13, 0),
    ("awb_sat", 55, 0, 10, 0), ("awb_dark", 55, 16, 10, 0),
    ("af_coring", 56, 0, 9, 0),
]
NREG = 64

# ---------------------------------------------------------------- control block
CTRL_BASE = 0x40
CTRL_REGS = [   # (name, word, access, description)
    ("ID", 0x40, "ro", "0x4F495350 ('OISP')"),
    ("VERSION", 0x41, "ro", "[31:24] major, [23:16] minor, [15:0] MAXW"),
    ("CTRL", 0x42, "rw", "bit0 COMMIT (1: request, reads 1 while pending), bit1 HOLD, bit2 FORCE (apply now), "
                         "bit3 RESET (datapath + frame counters, write alone; reads 1 while active)"),
    ("STATUS", 0x43, "ro", "bit0 commit pending, bit1 frame in flight, bit2 SOF held back for a commit"),
    ("IRQ_STATUS", 0x44, "w1c", "bit0 FRAME_DONE, bit1 SOF, bit2 COMMIT_DONE"),
    ("IRQ_ENABLE", 0x45, "rw", "same bits as IRQ_STATUS"),
    ("FRAMES_IN", 0x46, "ro", "frames accepted at the input (SOF count)"),
    ("FRAMES_OUT", 0x47, "ro", "frames completely sent at the output"),
    ("COMMIT_FRAME", 0x48, "ro", "FRAMES_IN when the last commit was applied"),
]
ISP_ID = 0x4F495350
VERSION_MAJOR, VERSION_MINOR = 1, 1      # 1.1: CTRL.RESET
CTRL_COMMIT, CTRL_HOLD, CTRL_FORCE, CTRL_RESET = 1, 2, 4, 8
IRQ_FRAME_DONE, IRQ_SOF, IRQ_COMMIT_DONE = 1, 2, 4


def cfa_code(pattern):
    t = _PATTERNS[pattern]
    return sum(t[y][x] << (2 * (2 * y + x)) for y in (0, 1) for x in (0, 1))


def field_values(cfg: HwConfig):
    """HwConfig -> {field name: integer} (derived values included)."""
    v = {"width": cfg.width, "height": cfg.height, "cfa": cfa_code(cfg.bayer_pattern),
         "dpc_thres": cfg.dpc_thres, "dpc_mean": int(cfg.dpc_mode == "mean"),
         "bl_alpha": cfg.bl_alpha, "bl_beta": cfg.bl_beta, "cnf_thres": cfg.cnf_thres,
         "nlm_dshift": cfg.nlm_dshift,
         "ee_g0": cfg.ee_gain[0], "ee_g1": cfg.ee_gain[1],
         "ee_t0": cfg.ee_thres[0], "ee_t1": cfg.ee_thres[1],
         "ee_clo": cfg.ee_emclip[0], "ee_chi": cfg.ee_emclip[1],
         "fcs_e0": cfg.fcs_edge[0], "fcs_e1": cfg.fcs_edge[1], "fcs_gain": cfg.fcs_gain,
         "hsc_sat": cfg.saturation, "bright": cfg.brightness, "contrast": cfg.contrast,
         "awb_sat": cfg.awb_sat, "awb_dark": cfg.awb_dark, "af_coring": cfg.af_coring}
    for n, x in zip(("bl_r", "bl_gr", "bl_gb", "bl_b"), cfg.bl):
        v[n] = x
    for n, x in zip(("awb_r", "awb_gr", "awb_gb", "awb_b"), cfg.awb_gain):
        v[n] = x
    for pre, m in (("ccm", cfg.ccm), ("csc", cfg.csc)):
        for k in range(9):
            v[f"{pre}_m{k}"] = m[k // 3][k % 3]
        for k in range(3):
            v[f"{pre}_o{k}"] = m[k][3]
    for t in range(25):
        v[f"bnf_dw{t}"] = cfg.bnf_dw[t // 5][t % 5]
    for k in range(4):
        v[f"bnf_rw{k}"] = cfg.bnf_rw[k]
    for k in range(3):
        v[f"bnf_t{k}"] = cfg.bnf_rthres[k]
    for t in range(15):
        v[f"ee_f{t}"] = cfg.edge_filter[t // 5][t % 5]
    sin_t, cos_t = b.hsc_tables()
    v["hsc_sin"], v["hsc_cos"] = int(sin_t[cfg.hue % 360]), int(cos_t[cfg.hue % 360])
    x0, y0, zw, zh = zone_grid(cfg.width, cfg.height, cfg.stats_x0, cfg.stats_y0, cfg.stats_zw, cfg.stats_zh)
    v.update(st_x0=x0, st_y0=y0, st_zw=zw, st_zh=zh)
    return v


def reg_words(cfg: HwConfig):
    """HwConfig -> {word address: 32-bit value} for all configuration registers."""
    vals = field_values(cfg)
    words = {}
    for name, word, lsb, width, signed in FIELDS:
        x = int(vals[name])
        lo, hi = (-(1 << (width - 1)), (1 << (width - 1)) - 1) if signed else (0, (1 << width) - 1)
        if not lo <= x <= hi:
            raise ValueError(f"{name} = {x} does not fit {'s' if signed else 'u'}{width}")
        words[word] = words.get(word, 0) | ((x & ((1 << width) - 1)) << lsb)
    return words


def table_writes(cfg: HwConfig):
    """(word address, value) writes for the gamma and NLM tables."""
    w = [(GAMMA_BASE + a, int(x)) for a, x in enumerate(b.gamma_lut(cfg.gamma))]
    w += [(NLM_LUT_BASE + a, int(x)) for a, x in enumerate(b.nlm_lut(cfg.nlm_h, cfg.nlm_dshift))]
    return w


def gen_sv():
    """Source of hw/rtl/top/isp_regs.sv."""
    reset = reg_words(HwConfig())
    out = ["// SPDX-License-Identifier: MIT",
           "// GENERATED by `python -m hwmodel.regmap` from hwmodel/regmap.py - do not edit.",
           "//",
           "// isp_regs - configuration registers (word addressed).  Double buffered:",
           "// the bus writes and reads back the pending bank r, the outputs come from",
           "// the active bank a, which is loaded from r when commit = 1.",
           "// Reset values = hwmodel.config.HwConfig() defaults.",
           "module isp_regs (",
           "  input  logic        clk, rst_n,",
           "  input  logic        commit,",
           "  input  logic        wr_en,",
           "  input  logic [15:0] wr_addr,",
           "  input  logic [31:0] wr_data,",
           "  input  logic [15:0] rd_addr,",
           "  output logic [31:0] rd_data,          // latency 1",
           ]
    for name, word, lsb, width, signed in FIELDS:
        out.append(f"  output logic {'signed ' if signed else ''}[{width - 1}:0] {name},")
    out[-1] = out[-1].rstrip(",")
    used = sorted({word for _, word, _, _, _ in FIELDS})
    out += [");", f"  logic [31:0] r [{NREG}], a [{NREG}];", "  always_ff @(posedge clk) begin", "    if (!rst_n) begin"]
    for wd in range(NREG):
        out.append(f"      r[{wd}] <= 32'h{reset.get(wd, 0):08x};")
    out += [f"    end else if (wr_en && wr_addr < {NREG}) r[wr_addr[5:0]] <= wr_data;", "  end",
            "  always_ff @(posedge clk) begin", "    if (!rst_n) begin"]
    for wd in used:
        out.append(f"      a[{wd}] <= 32'h{reset.get(wd, 0):08x};")
    out.append("    end else if (commit) begin")
    for wd in used:
        out.append(f"      a[{wd}] <= r[{wd}];")
    out += ["    end", "  end",
            f"  always_ff @(posedge clk) rd_data <= (rd_addr < {NREG}) ? r[rd_addr[5:0]] : 32'd0;"]
    for name, word, lsb, width, signed in FIELDS:
        out.append(f"  assign {name} = a[{word}][{lsb + width - 1}:{lsb}];")
    out += ["endmodule", ""]
    return "\n".join(out)


def gen_c_header():
    """Source of sw/include/openisp_regs.h (driver + firmware)."""
    reset = reg_words(HwConfig())
    sin_t, cos_t = b.hsc_tables()
    o = ["/* SPDX-License-Identifier: (GPL-2.0 OR MIT) */",
         "/* GENERATED by `python -m hwmodel.regmap` from hwmodel/regmap.py - do not edit. */",
         "#ifndef OPENISP_REGS_H", "#define OPENISP_REGS_H", "",
         "/* Word addresses; the AXI4-Lite byte offset is 4 x the word address. */",
         f"#define OISP_NREG              {NREG}",
         f"#define OISP_GAMMA_BASE        0x{GAMMA_BASE:04x}  /* 1024 x 8 bit, write only */",
         f"#define OISP_GAMMA_SIZE        1024",
         f"#define OISP_NLM_LUT_BASE      0x{NLM_LUT_BASE:04x}  /* 256 x 8 bit, write only */",
         f"#define OISP_NLM_LUT_SIZE      256",
         f"#define OISP_STATS_BAYER_BASE  0x{STATS_BAYER_BASE:04x}",
         f"#define OISP_STATS_AF_BASE     0x{STATS_AF_BASE:04x}",
         "#define OISP_NZONE             256  /* 16 x 16 zones, row major */",
         "#define OISP_ST_AWB_R          (OISP_STATS_BAYER_BASE + 0x000)",
         "#define OISP_ST_AWB_G          (OISP_STATS_BAYER_BASE + 0x100)  /* Gr + Gb */",
         "#define OISP_ST_AWB_B          (OISP_STATS_BAYER_BASE + 0x200)",
         "#define OISP_ST_AWB_N          (OISP_STATS_BAYER_BASE + 0x300)",
         "#define OISP_ST_AE_SUM         (OISP_STATS_BAYER_BASE + 0x400)",
         "#define OISP_ST_AE_HIST        (OISP_STATS_BAYER_BASE + 0x500)  /* 256 bins */",
         "#define OISP_ST_BAYER_FRAMES   (OISP_STATS_BAYER_BASE + 0x600)",
         "#define OISP_ST_AF             (OISP_STATS_AF_BASE + 0x000)",
         "#define OISP_ST_AF_FRAMES      (OISP_STATS_AF_BASE + 0x100)", "",
         "/* control block */"]
    for name, word, acc, desc in CTRL_REGS:
        o.append(f"#define OISP_{name:<22} 0x{word:04x}  /* {acc}: {desc} */")
    o += [f"#define OISP_ID_VALUE          0x{ISP_ID:08x}",
          f"#define OISP_VERSION_MAJOR     {VERSION_MAJOR}",
          f"#define OISP_VERSION_MINOR     {VERSION_MINOR}",
          f"#define OISP_CTRL_COMMIT       0x{CTRL_COMMIT:x}",
          f"#define OISP_CTRL_HOLD         0x{CTRL_HOLD:x}",
          f"#define OISP_CTRL_FORCE        0x{CTRL_FORCE:x}",
          f"#define OISP_CTRL_RESET        0x{CTRL_RESET:x}",
          "#define OISP_STATUS_PENDING    0x1",
          "#define OISP_STATUS_BUSY       0x2",
          "#define OISP_STATUS_SOF_HELD   0x4",
          f"#define OISP_IRQ_FRAME_DONE    0x{IRQ_FRAME_DONE:x}",
          f"#define OISP_IRQ_SOF           0x{IRQ_SOF:x}",
          f"#define OISP_IRQ_COMMIT_DONE   0x{IRQ_COMMIT_DONE:x}", "",
          "/* configuration fields: word, lsb, width, signed */",
          "enum oisp_field {"]
    for name, *_ in FIELDS:
        o.append(f"\tOISP_F_{name.upper()},")
    o += ["\tOISP_NFIELDS", "};", "",
          "struct oisp_field_desc {", "\tconst char *name;",
          "\tunsigned char word, lsb, width, is_signed;", "};", "",
          "#ifdef OISP_DEFINE_TABLES",
          "static const struct oisp_field_desc oisp_fields[OISP_NFIELDS] = {"]
    for name, word, lsb, width, signed in FIELDS:
        o.append(f'\t{{ "{name}", {word}, {lsb}, {width}, {signed} }},')
    o += ["};", "", "/* reset values of the configuration words (HwConfig() defaults) */",
          "static const unsigned int oisp_reset_words[OISP_NREG] = {"]
    for k in range(0, NREG, 4):
        o.append("\t" + " ".join(f"0x{reset.get(w, 0):08x}," for w in range(k, k + 4)))
    o += ["};", "", "/* HSC: round(256 sin(h)), round(256 cos(h)) for h = 0..359 degrees */",
          "static const short oisp_hsc_sin[360] = {"]
    for k in range(0, 360, 12):
        o.append("\t" + " ".join(f"{int(v)}," for v in sin_t[k:k + 12]))
    o += ["};", "static const short oisp_hsc_cos[360] = {"]
    for k in range(0, 360, 12):
        o.append("\t" + " ".join(f"{int(v)}," for v in cos_t[k:k + 12]))
    o += ["};", "#endif /* OISP_DEFINE_TABLES */", "", "#endif /* OPENISP_REGS_H */", ""]
    return "\n".join(o)


ROOT = __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__)))
GENERATED = {"hw/rtl/top/isp_regs.sv": gen_sv, "sw/include/openisp_regs.h": gen_c_header}

if __name__ == "__main__":
    import os
    for rel, gen in GENERATED.items():
        path = os.path.join(ROOT, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(gen())
        print("wrote", path)
