# Bit-accurate golden model (`hwmodel/`)

`hwmodel` is the integer reference for the hardware ISP. Every block is pure
integer arithmetic with explicit rounding and saturation, so the HLS/RTL
implementation can be compared **bit for bit** against it, block by block
(`python -m hwmodel RAW --stages` dumps each stage as `.npy`).

```
Bayer 10b : DPC -> BLC -> AAF -> AWB gain -> CNF
RGB       : CFA (10b) -> CCM (10b) -> Gamma (10b -> 8b)
YUV 8b    : CSC -> Y : NLM -> BNF -> EE -> BCC
                   UV: FCS (EE edge map) -> HSC
```

## Arithmetic conventions

| Rule | Definition |
|---|---|
| Right shift | `rshr(x, s) = (x + 2^(s-1)) >> s`, arithmetic (round half up) |
| Division | `(num + den/2) // den`, num >= 0, den > 0 (exact divider in HW) |
| Borders | mirror padding without edge repeat (numpy `reflect`); keeps the Bayer phase |
| Gains | Q10 (1024 = 1.0): AWB, CCM, CSC. Q8 (256 = 1.0): FCS, saturation, EE gains |
| Tables | Gamma (1024 x 8b), NLM weights (256 x 8b), HSC sin/cos: the generated table *is* the spec and is loaded into hardware as-is |

## Deviations from the floating-point openISP reference

Each item fixes a bug in `model/*.py` / `isp_pipeline.py` or replaces a
float/unbounded operation with a hardware-implementable one.

| Block | openISP | hwmodel | Why |
|---|---|---|---|
| Pipeline | CSC consumes the **CCM** output; gamma result is discarded | CSC consumes the gamma output | bug: gamma was bypassed |
| CCM | 10-bit result cast to `uint8` (wraps) | 10-bit out, saturated | bug: overflow |
| Gamma | 10b -> 10b LUT then `/4` | single 10b -> 8b LUT | one table, same function |
| BLC | adds `bl_*` | subtracts black level, saturates at 0 | conventional meaning |
| AAF | `scipy.correlate` default border (`reflect` = edge repeat) breaks the Bayer phase at borders | mirror padding | Bayer-safe border |
| CNF | 8x8 window `range(y-4, y+4)` but divides by 40/25/16 (9x9 counts); B-site path uses the wrong colour plane; thresholds in 8-bit scale on 10-bit data | symmetric 9x9 window, C1 = centre colour on both R and B sites, thresholds x4, fixed-point reciprocals | bugs |
| NLM | 6x6 asymmetric search (`range(2Ds+1-2ds-1)`), centre-skip test compares loop index with absolute coordinates, SSD + `exp` | symmetric (2r+1)^2 search (r = 2), 3x3 patch **SAD**, 256-entry weight LUT emulating `exp(-mad^2/h^2)`, centre weight = max neighbour weight | bug + ~10x fewer DSPs |
| BNF | float division, truncation | exact integer division, rounded | determinism |
| EE | overlapping/gapped `if` ranges in `emlut` | `|em| < t0: g0*em`, `t0 <= |em| <= t1: 0`, `|em| > t1: g1*em` | bug |
| FCS | `gain*U/256 + 128` applied to offset-binary U (no `-128`); slope/intercept give negative gains | `((U-128)*g >> 8) + 128`, linear ramp from `gain` at e0 to 0 at e1, (e1-e0) power of two | bugs |
| HSC | hue rotation computed then overwritten; saturation applied to the unrotated input | hue rotation (degrees, Q8 sin/cos) then saturation | bug |
| BCC | `(Y-127) * contrast` with contrast either `10` or `10/32` depending on code path | `Y + brightness + ((Y-128) * contrast_q5 >> 5)` | unambiguous |

## Verification against openISP

`tests/test_hwmodel.py` runs blocks whose behaviour is unchanged against the
original float code on a crop of `raw/test.RAW`:

| Block | Result |
|---|---|
| DPC (gradient) | identical |
| CFA (Malvar) | max abs diff 1 (openISP truncates, hwmodel rounds) |
| BNF | max abs diff 1 |
| AAF | interior max abs diff <= 0.5 (openISP keeps floats) |
| CSC (Y) | max abs diff 1 (Q10 coefficient rounding) |

Run: `python -m pytest tests`

## 3A statistics (`hwmodel/stats.py`)

Collected by hardware every frame on a 16 x 16 zone grid (window origin
`stats_x0/y0`, zone size `stats_zw/zh`, all even; 0 = automatic centred grid).
The A53 reads them at end of frame and runs the control loops in
`hwmodel/aaa.py` (reference implementation, not bit-exact).

| Stats | Tap point | Per zone (32 bit) | Global |
|---|---|---|---|
| AWB | Bayer after DPC + BLC, before WB gains | sum R, sum (Gr+Gb), sum B, valid quad count (quads with any channel >= `awb_sat` or < `awb_dark` skipped) | – |
| AE | same | sum of quad luma `L = (R+Gr+Gb+B+2) >> 2` | 256-bin histogram of `L >> 2` |
| AF | Y after CSC | sum of `|dY/dx| + |dY/dy|` (central differences, mirror borders) above `af_coring` | – |

Control loops (A53): gray-world AWB over zones with enough valid quads;
centre-weighted AE toward 18 % grey with highlight protection (target x0.7 when
> 2 % of quads clip) and damped, rate-limited steps; AF score = zone sum for a
contrast hill-climb.

Verification (`tests/test_stats3a.py`): the vectorised statistics are compared
bit for bit with an independent per-quad / per-pixel loop implementation;
AWB recovers a synthetic colour cast within 3 %; AE converges to its target
within 5 % in 12 frames from 8x over-exposure; AF ranks a sharp image above a
blurred one.
