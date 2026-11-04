# openISP on AMD Kria KV260

Hardware port of the openISP pipeline to the KV260 Vision AI Starter Kit,
using the onsemi AR1335 sensor on connector **J8 (IAS1)**, which goes straight
to the PL (no AP1302).

| Milestone | Content | Status |
|---|---|---|
| M0 | RAW capture: CSI-2 RX -> frame buffer -> DDR | on board: 1080p30 and 4K30 RAW capture at 30.00 fps |
| M1 | Bit-accurate integer golden model (`hwmodel/`) incl. 3A statistics | done |
| M2 | SystemVerilog blocks, 2 pixels/clock, 250 MHz, bit-exact vs M1 | done: all blocks + 3A stats + top, bit-exact, 250 MHz met OOC, 35% LUT (docs/hw/rtl.md) |
| M3 | CSI-2 RX -> ISP -> DDR -> PS DisplayPort (HDMI/DP) | on board at 1080p30 and 4K30 (ISP 30.04 fps measured, no CSI-2 overflow; display 1:1, a 4K frame shows its centre on a 1080p monitor): `m3_isp/` block design (video domain 200 MHz, timing met), ISP -> `isp_yuyv` -> frame buffer YUYV, live HDMI preview at 30 fps with AE/AWB (`m3_isp/live.sh`: zero-copy DMABUF -> DP video layer, `sw/app/oisp_view.c`); 250 MHz and NV12 todo |
| M4 | 4K30 timing closure | 4K30 runs at a 200 MHz video clock (WNS +0.147 ns); 250 MHz: -0.012 ns in the CSI-2 RX / SmartConnect, todo |
| M5 | 3A statistics + AE/AWB on the A53 | firmware done and host-tested (sw/, closed-loop simulation); on the board: driver + firmware build warning-free, firmware output bit-identical to x86, AE drives the AR1335 exposure/gain controls; ISP statistics need M3 |

## Layout

```
hw/rtl/common/   window_gen (KxK neighbourhoods, mirror borders), pipeline helpers
hw/rtl/bayer/    DPC BLC AAF AWB CNF CFA          hw/rtl/rgb/  CCM/CSC (mat3) Gamma
hw/rtl/yuv/      NLM BNF EE FCS+HSC+BCC, YUV chain
hw/rtl/stats/    AWB/AE + histogram, AF statistics
hw/rtl/top/      isp_top, isp_regs (generated from hwmodel/regmap.py)
hw/sim/          rtlsim.py (Verilator or xsim), tb_stream.sv + wrap/, streamtb.py
hw/syn/ooc.tcl   out-of-context synthesis + implementation on the K26 part
hw/kv260/
  driver/            openisp.ko - V4L2 sub-device + register / statistics ioctls (GPL-2.0)
  m0_raw_capture/    Vivado build script, pins, DT overlay, capture script
hwmodel/             golden model (MIT)          docs/hw/golden_model.md
sw/                  firmware: libopenisp + `openisp` tool / 3A daemon (C, MIT)   docs/sw.md
isptune/             PC tuning tool: calibration, bit-exact preview, live tuning (Python, MIT)
tests/               model, 3A, RTL, firmware and tuning tests (pytest; RTL tests need Verilator >= 5 or Vivado xsim)
```

## Requirements

* Vivado 2025.2 (KV260 board files included), on the build PC
* KV260 running Kria Ubuntu 22.04, `linux-headers-$(uname -r)`, `dtc`

## RTL simulation

```bash
python -m pytest tests                      # Verilator if found, else xsim
OPENISP_SIM=xsim python -m pytest tests     # force a simulator
```
Verilator is taken from `PATH` or `$VERILATOR_ROOT/bin`.

## M0: build and run

On the PC:
```bash
JOBS=4 vivado -mode batch -source hw/kv260/m0_raw_capture/build.tcl -tclargs build/m0
bootgen -arch zynqmp -process_bitstream bin -image <(echo 'all:{build/m0/kv260_m0_raw.bit}') -w
```

On the board (copy `hw/kv260` and `build/m0/kv260_m0_raw.bit.bin` over first):
```bash
cd driver && make && sudo make install && cd ..     # needs ../../sw/include (copy sw/ too)
dtc -@ -I dts -O dtb -o openisp-m0.dtbo m0_raw_capture/openisp-m0.dts
sudo mkdir -p /lib/firmware/xilinx/openisp-m0
sudo cp kv260_m0_raw.bit.bin /lib/firmware/xilinx/openisp-m0/openisp-m0.bit.bin
sudo cp openisp-m0.dtbo      /lib/firmware/xilinx/openisp-m0/openisp-m0.dtbo
echo '{ "shell_type": "XRT_FLAT", "num_slots": "1" }' | sudo tee /lib/firmware/xilinx/openisp-m0/shell.json
sudo xmutil unloadapp; sudo xmutil loadapp openisp-m0
./m0_raw_capture/capture_raw.sh 3840 2160 5 /tmp/ar1335_4k.raw
```

Back on the PC:
```bash
python -m hwmodel ar1335_4k.raw --width 3840 --height 2160 --pattern rggb -o out
```

### Why the RAW travels as "YUYV"

The stock Xilinx frame buffer driver has no Bayer memory formats. The PL
repacks each RAW10 pixel into a 16-bit YUYV8 carrier (Y = raw[7:0],
C = {6'b0, raw[9:8]}); in memory that is exactly a little-endian uint16 per
pixel (V4L2 `SRGGB10` layout). For 4:2:2 formats the frame buffer takes 2
components per pixel from `tdata[31:0]` of its 48-bit bus, so the two pixels
sit at `[15:0]` and `[31:16]` (the first build used 24-bit slots, and pixel 1
lost raw[9:8], seen on the board as byte 2 always 0). The `openisp` sub-device converts the Bayer media
bus code on its sink pad to `UYVY8_1X16` on its source pad, so the standard
V4L2 / xilinx-video stack streams it unchanged.

## M3: build and run

On the PC:
```bash
vivado -mode batch -source hw/kv260/m3_isp/build.tcl -tclargs build/m3      # VCLK=200 default
cd build/m3 && echo 'all:{kv260_m3_isp.bit}' > bit.bif && bootgen -arch zynqmp -process_bitstream bin -image bit.bif -w
```

On the board (driver, `sw/` and the overlay as for M0; boot clean, then load once):
```bash
dtc -@ -I dts -O dtb -o openisp-m3.dtbo m3_isp/openisp-m3.dts
sudo mkdir -p /lib/firmware/xilinx/openisp-m3
sudo cp kv260_m3_isp.bit.bin /lib/firmware/xilinx/openisp-m3/openisp-m3.bit.bin
sudo cp openisp-m3.dtbo      /lib/firmware/xilinx/openisp-m3/
echo '{ "shell_type": "XRT_FLAT", "num_slots": "1" }' | sudo tee /lib/firmware/xilinx/openisp-m3/shell.json
sudo xmutil unloadapp; sudo xmutil loadapp openisp-m3
openisp apply hw/kv260/tuning/ar1335_1080p.json        # tables (gamma, NLM) before streaming
bash m0_raw_capture/capture_raw.sh 1920 1080 5 /tmp/out.yuv   # YUYV frames (sets the formats)
bash m3_isp/live.sh start      # HDMI preview (oisp_view, stops gdm) + 3A daemon; live.sh stop restores gdm
```
