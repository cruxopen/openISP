# Driver, firmware and tuning software

```
 PC                                   KV260 (A53, Kria Ubuntu)                 PL
 ───────────────────────────          ───────────────────────────────          ───────────────
 isptune (Python)                     openisp run  (3A daemon, C)              isp_axil
   calibrate RAW captures               AE / AWB / CCM / AF loops                AXI4-Lite regs
   offline preview = hwmodel   ─push─▶  live tuning socket          ioctl ──▶  openisp.ko ──▶ shadow regs, commit
   export tuning.json          ◀stats─  libopenisp (cfg -> regs)    ◀stats──  (IRQ: copy stats) ◀ 3A statistics
                                        V4L2 controls ──▶ sensor (exposure, gain, focus)
```

| Part | Location | Language / licence |
|---|---|---|
| Register map (single source) | `hwmodel/regmap.py` → `hw/rtl/top/isp_regs.sv`, `sw/include/openisp_regs.h` | Python, generated |
| Kernel driver | `hw/kv260/driver/openisp.c` | C, GPL-2.0 |
| Driver ABI | `sw/include/openisp_uapi.h` | GPL-2.0 WITH syscall note OR MIT |
| Firmware library + tool / 3A daemon | `sw/lib/`, `sw/app/openisp.c` | C99, MIT |
| Tuning tool | `isptune/` | Python, MIT |
| 3A reference algorithms | `hwmodel/aaa.py` (firmware reference section) | Python, MIT |

## Hardware interface (what software sees)

* **AXI4-Lite** (`isp_axil`, 18-bit byte address): word *n* is at byte offset 4*n*.
* **Shadow registers.** Configuration words 0x00–0x3F are double-buffered. Software writes the pending bank.
  * `CTRL.COMMIT` copies the pending bank to the active bank once no frame is inside the pipeline.
  * Until then the next start of frame is held at the input.
  * Every frame is therefore processed with exactly one configuration.
  * `CTRL.HOLD` blocks the commit while a register sequence is being written. `CTRL.FORCE` applies at once, for recovery.
  * `CTRL.RESET` (hardware 1.1) resets the datapath and the frame counters and drops a pending commit; registers and tables are kept. The driver issues it at every stream on. Without it a frame truncated by an aborted stream (seen on the board: the frame buffer stopped first, the CSI-2 RX line buffer overflowed) stays in the pipeline, FRAMES_IN never equals FRAMES_OUT again and the next commit holds the input forever.
* **Gamma and NLM tables** are written directly, not shadowed. Load them before streaming; a change while streaming affects at most one frame.
* **Interrupt** (level): `IRQ_STATUS & IRQ_ENABLE`, write 1 to clear. The bits are FRAME_DONE (a frame has left the ISP), SOF and COMMIT_DONE.
* **Statistics** are double-buffered in hardware. At FRAME_DONE the last frame's statistics can be read for one frame time.
  * The read is checked against the frame counters (seqlock).

Control block (word 0x40–0x48): `ID`, `VERSION` (with MAXW), `CTRL`, `STATUS`, `IRQ_STATUS`, `IRQ_ENABLE`, `FRAMES_IN`, `FRAMES_OUT`, `COMMIT_FRAME`.

## Kernel driver (`openisp.ko`)

The driver is a V4L2 sub-device in the xilinx-video graph, `csiss → openisp → frmbuf_wr`.

* **Without `reg`** (M0 bitstream): it only converts the format, as before.
* **With `reg`**, it also does the following:
  * **At stream on:** programs the frame size, the Bayer order (from the sink media-bus code) and an automatic centred 16 × 16 statistics grid, then commits.
  * **On FRAME_DONE:** a threaded IRQ copies the statistics (about 1800 words) into a double buffer and wakes up waiters.
  * **ioctls** on `/dev/v4l-subdevN`, in the V4L2 private range:

| ioctl | Purpose |
|---|---|
| `OPENISP_IOC_INFO` | ID, version, sink format, frame counters |
| `OPENISP_IOC_PARAMS` | Up to 4096 `{addr, value}` writes (config words and tables only, range checked). With `OPENISP_PARAMS_COMMIT`, written as HOLD, the writes, then COMMIT. |
| `OPENISP_IOC_STATS` | Block (with a timeout) until the statistics of a frame newer than `frame` are available, then copy them (`struct openisp_stats`, 7.2 KB) |
| `OPENISP_IOC_READ_REG` | Debug read of one word |

Device tree (M3 overlay):
```dts
openisp: isp@a0000000 {
    compatible = "openisp,isp-1.0";
    reg = <0x0 0xa0000000 0x0 0x40000>;
    interrupt-parent = <&gic>;
    interrupts = <0 89 4>;                 /* pl_ps_irq0[0], level high */
    clocks = <&clk_250M>;                   /* optional: enabled at probe */
    openisp,output-format = "nv12";
    ports { /* port@0 sink from csiss, port@1 source to frmbuf_wr */ };
};
```
Build on the board: `cd hw/kv260/driver && make && sudo make install`. The `sw/include` directory must be next to `hw/`, or set `OPENISP_INC`.

The driver was written against Linux 5.15 (Kria Ubuntu 22.04), with version guards for the 6.8 and 6.11 API changes. It is **not compiled yet**: no matching kernel tree is available on the build PC, so the first build on the board is the compile check.

## Firmware: `libopenisp` and the `openisp` tool

```
make -C sw                                   # native: on the KV260, or the host for tests
make -C sw CROSS_COMPILE=aarch64-linux-gnu-  # cross build
sudo make -C sw install                      # /usr/local/bin/openisp + systemd unit
```

| Command | Purpose |
|---|---|
| `openisp info` | Hardware ID, version, format, frame counters |
| `openisp apply T.json [--no-tables]` | Write a tuning file once (static ISP, no 3A) |
| `openisp stats [-n N] [--save DIR]` | Print a statistics summary; save raw `struct openisp_stats` files |
| `openisp reg ADDR [VALUE]` | Read or write a register |
| `openisp run T.json [--sensor NAME] [--listen unix:PATH] [-v]` | The 3A daemon |
| `openisp pack T.json` | Print the register writes a tuning file produces (no hardware) |

Device selection with `-d`:
* `/dev/v4l-subdevN`: the default is auto-detected by the name `openisp`.
* `mem:0xA0000000`: bring-up through `/dev/mem` without the driver. Statistics are polled.
* `sim:LOG:DIR:WxH`: host tests. Writes go to LOG and statistics are read from `DIR/stats_<n>.bin`.

### The 3A daemon (`openisp run`)

For each statistics frame (pre-gain Bayer statistics, so AWB is open loop):

1. **AWB.**
   * Gray world over the zones with at least `min_quads` valid quads gives the white point in log2-gain space.
   * The white point is pulled to within `locus_dist` of the illuminant polyline (calibrated illuminants sorted by CCT).
   * It is then smoothed with `speed`.
   * The CCM is interpolated between the two neighbouring illuminants. Rows keep their sum, so white stays white.
   * The CCT is reported, interpolated in mired.
2. **AE**, at most every `delay + 1` frames so a new exposure is visible before the next decision.
   * The centre-weighted mean quad luma × the current digital gain is compared with `target`. With more than `hl_frac` of quads in histogram bins ≥ `hl_bin`, the target drops to `target × hl_scale`.
   * The step is damped (`damping`) and limited to `ratio_max`.
   * The total exposure is split as follows:
     * integration lines first, in multiples of `flicker_lines` when that is set;
     * then sensor gain: code = gain × `gain_1x`, or, with `gain_table`, the largest table step not above the needed gain (for drivers whose gain control is an index);
     * then digital gain, applied through the four WB gain registers.
   * Lines and gain go to the sensor sub-device: the controls named by `sensor.exposure_ctrl` / `sensor.gain_ctrl`, else `V4L2_CID_EXPOSURE` (or a control named `*integration_time*` / `*exposure*`) and `V4L2_CID_ANALOGUE_GAIN` (else `V4L2_CID_GAIN`).
3. **AF** (optional): a contrast sweep `pos_min..pos_max` over the ROI zones, then hold. A score change of more than 30 % restarts the sweep. The lens is driven through `V4L2_CID_FOCUS_ABSOLUTE`.
4. **Registers.** All configuration words are written with commit, so they apply at the next frame boundary. The tables are written only when they change.

**Live tuning** uses a line protocol on a Unix socket. The service default is `/run/openisp/live.sock`; the PC reaches it with `ssh -N -L 5555:/run/openisp/live.sock board`. Commands:

* `get NAME`
* `set NAME VALUES` (every `HwConfig` field, plus `gamma_lut` / `nlm_lut`)
* `aaa ae|awb|af on|off`
* `state` (AE/AWB/AF state as JSON)
* `stats` (the last statistics as JSON)
* `dump` (the current ISP configuration)
* `load {tuning json}`

The frame size and Bayer order always come from the driver. An invalid value is rejected and the old configuration is kept.

### AR1335 on the KV260 (measured on the board)

The stock Kria driver (`ar1335.ko`, Xilinx) has no standard exposure control. `isptune init --sensor ar1335` writes this profile:

| | Control | Behaviour |
|---|---|---|
| Exposure | `coarse_integration_time` (private, 0x009a0d18) | lines, register 0x0202. Frame length 3150 lines at 4K30, 1573 at 1080p; `lines_max` = frame length − 32 (the driver's own default) |
| Gain | `gain` (`V4L2_CID_GAIN`, 0..5) | index into `global_gain` 0x305E settings 0x2015, 0x2025, 0x2035, 0x2BBF, 0x573F, 0xAE3F: 2× per step (analog coarse steps, then sensor digital gain). `gain_table` = [1, 2, 4, 8, 16, 32] |
| Focus | none | fixed-focus module, AF stays off |

At stream on the driver rewrites 0x0202 from its mode table (0x305E is kept), so the daemon applies exposure after streaming starts.

Known driver problem: after a 1080p → 4K mode change the driver's control lock stayed held; every control ioctl on the sensor then blocks in the kernel until a reboot. Pick the mode once per boot.

## Tuning tool (`isptune`)

```bash
python -m isptune init -o t.json --width 3840 --height 2160 --pattern grbg --sensor ar1335
python -m isptune blc dark.raw -t t.json                         # lens capped
python -m isptune chart d65.raw -t t.json --corners TLx,TLy,TRx,TRy,BRx,BRy,BLx,BLy --illuminant D65 --set-static
python -m isptune chart a.raw   -t t.json --corners ... --illuminant A
python -m isptune noise d65.raw -t t.json --corners ...
python -m isptune preview scene.raw -t t.json --auto -o scene.png  # bit-exact with the hardware
python -m isptune export -t t.json -o board.json [--gamma-curve srgb]
scp board.json kv260:/etc/openisp/tuning.json
python -m isptune live --port 5555 set saturation 300              # live, through the ssh tunnel
python -m isptune live --port 5555 push board.json
```

| Step | Method |
|---|---|
| `blc` | Per-channel mean of a dark frame, excluding hot pixels (> median + 10 MAD). Reports read noise, the hot-pixel count and a DPC threshold of 8 σ. |
| `chart` | 24 patches located from the 4 outer corners of the patch grid; per-patch means of the raw Bayer channels minus black. <br>WB gains come from the unclipped neutral patches. <br>CCM: least squares to linear sRGB with rows summing to 1, then refined to minimise the mean CIEDE2000. Quantised to Q10, rows exactly 1024. <br>Reports ΔE2000 for WB only and with the CCM. The result is stored as an illuminant (gains, CCM, CCT). |
| `noise` | Raw noise model `var = a·level + b` from the gray patches, and the Y noise σ after CSC. Suggests `nlm_h = 1.5σ` and `bnf_rthres = 8σ, 3σ, σ` as a starting point. |
| `preview` | `hwmodel.pipeline.run` with the tuning file, so the output equals the hardware bit for bit. <br>`--auto` first runs the firmware AWB (with CCM interpolation) and AE as digital gain. |
| `export` | Adds `gamma_lut` / `nlm_lut` (optionally an sRGB curve) and checks every register range and the 3A settings. |

The tuning file format is described in `isptune/tuning.py`.

## Verification (host, `python -m pytest tests`)

| Test | Content |
|---|---|
| `test_fw.py` | Firmware vs Python. <br>• Tuning file → register words and tables: identical to `hwmodel.regmap` for random configurations, including the computed gamma/NLM tables; out-of-range values are rejected. <br>• Daemon on recorded statistics: AE lines, gain code, digital gain, WB registers, CCM and CCT equal `hwmodel.aaa` frame by frame. The last committed register set equals `regmap.reg_words`. <br>• Closed loop: Python plays the sensor. AE reaches the target within 5 % through integration time plus analog gain, and AWB cancels the colour cast. |
| `test_tuning.py` | Synthetic sensor with a known CCM, colour cast, black level, noise and hot pixels. <br>• Black level exact; WB gains within 1 %; CCM within 0.03; mean ΔE2000 below 1. <br>• Neutral patches come out neutral in the bit-exact preview; the exported file is loaded identically by the firmware. <br>• Live tuning against the real daemon: set, get, invalid values rejected, push, and the resulting register writes. |
| `test_rtl_top.py` | Control block in RTL. <br>• Commit while the previous frame is still in the pipeline: every frame is still bit exact. Negative check: FORCE corrupts the tail of the running frame. <br>• HOLD / FORCE, the IRQ enable / clear and the frame counters. <br>• A truncated input frame followed by RESET and a new configuration: the next frame is bit exact, the counters restart. |
| `test_rtl_axil.py` | AXI4-Lite bridge with a random master (independent AW/W, random BREADY/RREADY), and elaboration of `isp_axil` |
