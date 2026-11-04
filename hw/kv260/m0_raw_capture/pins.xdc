# SPDX-License-Identifier: MIT
# KV260 carrier pin assignments (taken from the Kria kv260_bist platform).
# MIPI IAS1 (J8) pins are placed by the CSI-2 RX board flow (mipi_csi_ias).

# Sensor / ISP control GPIOs (LVCMOS33, HDA bank)
set_property PACKAGE_PIN J11 [get_ports {GPIO_tri_o[0]}] ;# HDA02 AP1302_RST_B
set_property PACKAGE_PIN J10 [get_ports {GPIO_tri_o[1]}] ;# HDA03 AP1302_STANDBY
set_property PACKAGE_PIN K13 [get_ports {GPIO_tri_o[2]}] ;# HDA04 IAS_XSHUTDOWN_B (J8 sensor)
set_property PACKAGE_PIN F11 [get_ports {GPIO_tri_o[3]}] ;# HDA09 RPi camera enable
set_property IOSTANDARD LVCMOS33 [get_ports {GPIO_tri_o[*]}]
set_property SLEW SLOW [get_ports {GPIO_tri_o[*]}]
set_property DRIVE 4 [get_ports {GPIO_tri_o[*]}]

# I2C -> PCA9546 switch (ch0: AP1302, ch1: IAS1 sensor, ch2: RPi camera)
set_property PACKAGE_PIN G11 [get_ports iic_scl_io]
set_property PACKAGE_PIN F10 [get_ports iic_sda_io]
set_property IOSTANDARD LVCMOS33 [get_ports iic_*]
set_property SLEW SLOW [get_ports iic_*]
set_property DRIVE 4 [get_ports iic_*]

# Fan enable (TTC0 PWM)
set_property PACKAGE_PIN A12 [get_ports {fan_en_b}]
set_property IOSTANDARD LVCMOS33 [get_ports {fan_en_b}]
set_property SLEW SLOW [get_ports {fan_en_b}]
set_property DRIVE 4 [get_ports {fan_en_b}]

set_property BITSTREAM.CONFIG.OVERTEMPSHUTDOWN ENABLE [current_design]
