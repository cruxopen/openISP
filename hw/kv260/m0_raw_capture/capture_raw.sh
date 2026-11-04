#!/bin/bash
# SPDX-License-Identifier: MIT
# Capture RAW10 Bayer frames from the AR1335 on J8 with the M0 design.
#
#   [SENSOR_CTRLS=name=v,...] capture_raw.sh WIDTH HEIGHT [FRAMES] [OUT] [SKIP]
#   e.g. capture_raw.sh 3840 2160 5 /tmp/ar1335_4k.raw
#        SENSOR_CTRLS=coarse_integration_time=800,gain=1 capture_raw.sh 1920 1080 2 /tmp/w.raw
# SENSOR_CTRLS are set 0.5 s after stream on (the AR1335 driver rewrites the
# integration time from its mode table at stream on); keep SKIP >= 20.
#
# Each frame is WIDTH*HEIGHT little-endian uint16 (RAW10 in bits 9:0,
# V4L2 SRGGB10 layout); frames are concatenated. Feed to the golden model:
#   python -m hwmodel OUT --width W --height H --pattern rggb [--frame N]
set -euo pipefail
W=${1:?width}; H=${2:?height}; N=${3:-1}; OUT=${4:-/tmp/raw_${W}x${H}.raw}; SKIP=${5:-30}

MD=$(for m in /dev/media*; do media-ctl -d "$m" -p 2>/dev/null | grep -q "openisp" && echo "$m" && break; done)
[ -n "$MD" ] || { echo "openisp media graph not found (is the M0 app loaded?)"; exit 1; }
ent() { media-ctl -d "$MD" -e "$1"; }
SEN=$(media-ctl -d "$MD" -p | sed -n 's/.*entity [0-9]*: \(ar1335 [^ ]*\) .*/\1/p' | head -1)
CSI=$(media-ctl -d "$MD" -p | sed -n 's/.*entity [0-9]*: \([0-9a-f]*\.csiss\) .*/\1/p' | head -1)
ISP=$(media-ctl -d "$MD" -p | sed -n 's/.*entity [0-9]*: \([^ ]*\(openisp\|\.isp\)\) (2 pads.*/\1/p' | head -1)
VID=$(media-ctl -d "$MD" -p | awk '/openisp_vcap|vcap/{f=1} f&&/device node name/{print $4; exit}')

RAW10=0x300f   # MEDIA_BUS_FMT_SRGGB10_1X10
v4l2-ctl -d "$(ent "$SEN")" --set-subdev-fmt pad=0,width=$W,height=$H,code=$RAW10 >/dev/null
for p in 0 1; do v4l2-ctl -d "$(ent "$CSI")" --set-subdev-fmt pad=$p,width=$W,height=$H,code=$RAW10,field=none >/dev/null; done
v4l2-ctl -d "$(ent "$ISP")" --set-subdev-fmt pad=0,width=$W,height=$H,code=$RAW10,field=none >/dev/null
v4l2-ctl -d "$VID" --set-fmt-video=width=$W,height=$H,pixelformat=YUYV >/dev/null

media-ctl -d "$MD" -p | grep -E "^- entity|fmt:" | sed 's/colorspace.*//'
if [ -n "${SENSOR_CTRLS:-}" ]; then
  (sleep 0.5; v4l2-ctl -d "$(ent "$SEN")" -c "$SENSOR_CTRLS") &
fi
v4l2-ctl -d "$VID" --stream-mmap --stream-count="$N" --stream-skip="$SKIP" --stream-to="$OUT" 2>&1 \
  | tr '\r' '\n' | grep -E "fps|returned" | tail -1 || true
wait
ls -l "$OUT"
