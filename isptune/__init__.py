# SPDX-License-Identifier: MIT
"""isptune - PC tuning tool of the openISP hardware ISP.

Calibration from RAW captures (black level, white balance + CCM per
illuminant from a ColorChecker, noise -> denoise strength), bit-exact
offline preview through hwmodel, tuning file export for the firmware, and
live tuning of the 3A daemon on the board.  Run `python -m isptune -h`."""
