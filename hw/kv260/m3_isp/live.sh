#!/bin/bash
# SPDX-License-Identifier: MIT
#
# HDMI/DP preview of the openISP output with the 3A daemon (KV260, M3).
#
#   live.sh start [TUNING.json]   (re)start the preview and the 3A daemon
#   live.sh stop                  stop both and give the screen back to gdm
#
# The preview is oisp_view (sw/app/oisp_view.c): the V4L2 capture buffers are
# exported as DMABUFs and scanned out directly by the ZynqMP DisplayPort video
# layer (YUYV, hardware colour conversion) - no CPU copy, no GPU (the Mali
# userspace driver is not installed; GL fell back to software rendering at a
# few fps).  It needs DRM master, so gdm is stopped while the preview runs.
# `live.sh stop` (or `sudo systemctl start gdm`) brings the login screen back.
# Snapshot on request: `touch /tmp/snap.yuv.req` -> the next frame is written
# to /tmp/snap.yuv (YUYV, full frame); oisp_view prints the displayed frame
# rate to /tmp/view.log every 5 s.  Live tuning socket: /tmp/openisp.sock
#   (PC: ssh -N -L 5555:/tmp/openisp.sock ubuntu@kv260)
# Logs: /tmp/view.log, /tmp/3a.log
set -u
DIR=$(cd "$(dirname "$0")" && pwd)
BIN=${BIN:-$DIR/../../../sw/build}
[ -x "$BIN/openisp" ] || BIN=$HOME/oisp/sw/build
T=${2:-${TUNING:-$HOME/oisp/ar1335_1080p.json}}

stop_video() {
  sudo -n pkill -INT -x oisp_view 2>/dev/null
  sudo -n pkill -INT -x gst-launch-1.0 2>/dev/null
  pkill -x openisp 2>/dev/null
  for i in $(seq 1 10); do pgrep -x oisp_view >/dev/null || pgrep -x gst-launch-1.0 >/dev/null || break; sleep 0.5; done
  sleep 0.5
}

free_display() {
  systemctl is-active -q gdm && sudo -n systemctl stop gdm
  sudo -n pkill -f "Xorg :0 .*-nocursor" 2>/dev/null
  for i in $(seq 1 10); do pgrep -x Xorg >/dev/null || break; sleep 0.5; done
}

case "${1:-}" in
stop)
  stop_video
  sudo -n systemctl start gdm
  exit 0 ;;
start) ;;
*) echo "usage: $0 start [tuning.json] | stop"; exit 1 ;;
esac

stop_video
free_display
sudo -n setsid "$BIN/oisp_view" -s /tmp/snap.yuv > /tmp/view.log 2>&1 < /dev/null &
sleep 3      # stream on first: the sensor driver rewrites the exposure at stream on
setsid "$BIN/openisp" run "$T" --sensor ar1335 --listen unix:/tmp/openisp.sock -v \
  > /tmp/3a.log 2>&1 < /dev/null &
sleep 1
echo "preview + 3A started (logs /tmp/view.log /tmp/3a.log)"
