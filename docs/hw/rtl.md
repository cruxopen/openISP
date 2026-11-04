# SystemVerilog implementation (`hw/rtl/`)

Every block is hand-written SystemVerilog (no HLS) and is **bit-exact** with the
integer golden model `hwmodel/` (see [golden_model.md](golden_model.md)).
2 pixels per clock, 250 MHz target on the K26 SOM (XCK26-SFVC784-2LV).

```
RAW10 -> DPC -> BLC -> AAF -> AWB -> CNF -> CFA -> CCM -> Gamma -> CSC -> NLM -> BNF -> EE -> BCC -> YUV444
                 |                                                  |              UV FIFO -> FCS -> HSC
                 +-> stats_bayer (AWB / AE)                          +-> stats_af (AF)
```

## Conventions

| Item | Rule |
|---|---|
| Stream | AXI4-Stream video, TUSER = start of frame, TLAST = end of line, 2 pixels per beat, pixel 0 in the LSBs |
| Pixel formats | Bayer `{p1, p0}` 2 x 10 bit; RGB `{B, G, R}` per pixel; YUV `{V, U, Y}` per pixel |
| Bayer phase | `cfa` register: channel id (0 R, 1 Gr, 2 Gb, 3 B) of pixel (y odd, x odd) at bits `[2*(2*yodd + xodd) +: 2]` |
| Borders | `window_gen` mirror (numpy `reflect`), shared by every neighbourhood block |
| Flow control | every block: free-running pipeline (no clock enable) + output FIFO with credits (`pipe_out`), full rate, any TVALID / TREADY pattern; `axis_skid` register slice between blocks |
| Frame size | latched at SOF; width multiple of 2, >= 6 and <= MAXW (3840); height > window radius; statistics need >= 32 x 32 |
| Configuration | double buffered: written any time, applied at a frame boundary by `CTRL.COMMIT` (`isp_ctrl`); gamma / NLM tables are written directly (load before streaming) |

## Blocks

| Block | File | Window | Latency (cycles after the window) | Notes |
|---|---|---|---|---|
| window generator | `common/window_gen.sv` | KH x KW | ~R lines | 2R line buffers, row replay for the bottom mirror |
| DPC | `bayer/isp_dpc.sv` | 5x5 | 3 | gradient / mean mode |
| BLC | `bayer/isp_blc.sv` | point | 3 | Gr/Gb crosstalk uses the R/B pixel of the same beat |
| AAF | `bayer/isp_aaf.sv` | 5x5 | 2 | |
| AWB gain | `bayer/isp_awb.sv` | point | 2 | Q10, 14-bit gains |
| CNF | `bayer/isp_cnf.sv` | 9x9 | 11 | column sums shared by both pixels |
| CFA | `bayer/isp_cfa.sv` | 5x5 | 3 | Malvar, 10-bit RGB out |
| CCM / CSC | `rgb/isp_mat3.sv` | point | 3 | 3x3 + offset, Q10, signed 16-bit coefficients |
| Gamma | `rgb/isp_gamma.sv` | point | 1 | 1024 x 8 LUT, 6 block-RAM copies |
| NLM | `yuv/isp_nlm.sv` | 7x7 | 17 | 24 SADs from shared column sums, 48-port distributed LUT, pipelined divider |
| BNF | `yuv/isp_bnf.sv` | 5x5 | 13 | 25 x (dw * rw * p), pipelined divider |
| EE | `yuv/isp_ee.sv` | 3x5 | 7 | also outputs the edge map (saturated to +-2047) |
| FCS + HSC + BCC | `yuv/isp_yuvpost.sv` | point | 9 | FCS ramp length must be a power of two, `fcs_e1 <= 2047` |
| YUV chain | `yuv/isp_yuv.sv` | - | - | Y through NLM/BNF/EE, UV through an elastic FIFO (7 lines, 4 URAM), re-joined in order |
| AWB/AE stats | `stats/stats_bayer.sv` | point (quad) | - | observer, never stalls; even row kept in a line buffer |
| AF stats | `stats/stats_af.sv` | 3x3 | - | centre of the NLM window, no extra line buffers |
| control | `top/isp_ctrl.sv` | - | - | ID / version, commit (HOLD, FORCE), SOF hold while a commit waits, datapath soft reset (RESET), IRQ, frame counters |
| top | `top/isp_top.sv`, `top/isp_regs.sv` | | | `isp_regs.sv` (pending + active bank) is generated from `hwmodel/regmap.py` |
| block-design top | `top/isp_axil.sv`, `common/axil_regbus.sv` | | | AXI4-Lite control, AXI4-Stream video in / out, level interrupt |

Common helpers: `pipe_out` (free-running pipeline output FIFO + credits, no clock enable), `div_round_pipe`
(restoring division, round half up, 1 bit per stage), `lut_mem` (replicated
LUT), `fifo_fwft`, `axis_skid`, `row_parity`.

## Implementation results

`isp_top` (MAXW = 3840), out-of-context synthesis + place + route, Vivado 2025.2,
XCK26-SFVC784-2LV-C, 4.0 ns clock (`hw/syn/ooc.tcl`):

| Resource | Used | K26 | % |
|---|---|---|---|
| LUT | 40 968 | 117 120 | 35.0 |
| FF | 33 942 | 234 240 | 14.5 |
| BRAM36 | 51.5 | 144 | 35.8 |
| URAM | 4 | 64 | 6.3 |
| DSP | 196 | 1 248 | 15.7 |

Timing: **all constraints met at 250 MHz** (setup WNS +0.037 ns, hold WHS +0.029 ns).
With the control block, shadow registers and the AXI4-Lite bridge (`isp_axil`):
LUT 41 090 (35.1 %), FF 35 661 (15.2 %), BRAM36 51.5, URAM 4, DSP 196;
WNS +0.049 ns, WHS +0.029 ns, all constraints met.
Largest blocks: NLM 14.8k LUT, BNF 4.9k LUT / 100 DSP, CNF 5.7k LUT.
At 2 pixels/clock and 250 MHz the pipeline processes 500 Mpixel/s: 4K30
needs 249 Mpixel/s of active video, 1080p60 124 Mpixel/s.

```
vivado -mode batch -source hw/syn/ooc.tcl -tclargs build/ooc isp_top 4.0 <rtl files>
```

Timing techniques used: free-running pipelines (no clock-enable fan-out),
registered window-generator output, register slices between blocks, constant
multiplications and roundings folded into DSP adders, no combinational
TREADY path longer than one block.

## 3A statistics hardware

* 16 x 16 zone grid from counters (`zone_cnt`), no dividers.
* `zone_acc`: 16 accumulators per statistic for the current zone row; at the
  last row of a zone row they are snapshotted and drained into block RAM
  (16 cycles), so the next zone row starts accumulating immediately.
* Histogram: block-RAM read-modify-write with one-cycle forwarding; three
  banks (written / published / cleared in idle cycles), no clear time between frames.
* Double buffering: every write carries the id of its frame; the read port
  always shows the last **completed** frame. Software reads the statistics
  within one frame time after the frame-count register increments.

## Register map

`hwmodel/regmap.py` is the single source (word addresses; AXI byte address = 4 x):

| Word address | Content |
|---|---|
| `0x0000 - 0x0038` | configuration fields (`FIELDS`), reset = `HwConfig()` defaults; reads return the pending bank |
| `0x0040 - 0x0048` | control block: ID, VERSION, CTRL, STATUS, IRQ_STATUS, IRQ_ENABLE, FRAMES_IN, FRAMES_OUT, COMMIT_FRAME (`CTRL_REGS`) |
| `0x0400 - 0x07FF` | gamma LUT (1024 x 8, write only) |
| `0x0800 - 0x08FF` | NLM weight LUT (256 x 8, write only) |
| `0x1000 - 0x14FF` | AWB/AE zones: awb_r, awb_g, awb_b, awb_n, ae_sum (256 each) |
| `0x1500 - 0x15FF` | AE histogram (256 bins) |
| `0x1600` | completed frames (AWB/AE) |
| `0x2000 - 0x20FF` | AF zones |
| `0x2100` | completed frames (AF) |

`regmap.reg_words(cfg)` / `regmap.table_writes(cfg)` turn a `HwConfig` into
register writes (derived values: `cfa`, HSC sin/cos, automatic zone grid).
Regenerate the RTL register file and the C header after a change: `python -m hwmodel.regmap`.

Commit sequence (driver / firmware): `CTRL = HOLD`, write the words, `CTRL = COMMIT`.
The active bank is loaded when `FRAMES_IN == FRAMES_OUT` (no frame inside the
pipeline); meanwhile the next SOF beat is held at the input (`s_tready` low), so
the vertical blanking must be longer than the pipeline drain time (~16 lines at
the ISP clock rate; AR1335 modes have > 40 lines).  See [../sw.md](../sw.md).

## Verification

File-driven testbenches (`hw/sim/tb_stream.sv` + per-block `hw/sim/wrap/*.sv`),
Python drives stimulus, golden model and comparison (`hw/sim/streamtb.py`).
Runs on **Verilator >= 5** (default) and **Vivado xsim** (`OPENISP_SIM=xsim`).

| Test | Content |
|---|---|
| `test_rtl_window_gen.py` | 4 window sizes, min/max frame sizes, 3 flow-control patterns, throughput |
| `test_rtl_bayer.py` | DPC BLC AAF AWB CNF CFA: random configs and Bayer phases, 8 frame sizes up to 3840 wide |
| `test_rtl_rgb.py` | CCM CSC Gamma (table reloaded between frames) |
| `test_rtl_yuv.py` | NLM BNF EE FCS/HSC/BCC, incl. arbitrary LUTs and zero weights |
| `test_rtl_stats.py` | AWB/AE/histogram and AF read back after every frame (double buffer, bank clearing) |
| `test_rtl_top.py` | whole pipeline vs `hwmodel.pipeline.run`: openISP `test.RAW` crops, 3840 x 34, random full configurations + back-pressure; output stream and all statistics; commit while the previous frame is in flight (bit exact), HOLD / FORCE, IRQ, frame counters, recovery from a truncated frame with RESET |
| `test_rtl_axil.py` | AXI4-Lite bridge with a random master; `isp_axil` elaboration |

Every comparison checks every output bit plus TUSER/TLAST. Run:
`python -m pytest tests` (Verilator in PATH) or `OPENISP_SIM=xsim python -m pytest tests`.
