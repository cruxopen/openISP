# SPDX-License-Identifier: MIT
#
# M3: KV260 camera pipeline with the openISP hardware ISP.
#
#   AR1335 (IAS1/J8, 4-lane MIPI, RAW10)
#     -> MIPI CSI-2 RX Subsystem (2 PPC)
#     -> AXIS subset converter (RAW10 2 px, TUSER = SOF, no TDEST) -> AXIS FIFO
#     -> isp_axil  (openISP, AXI4-Lite registers at 0x8004_0000, IRQ = GIC SPI 92)
#     -> isp_yuyv  (YUV 4:4:4 -> YUYV 4:2:2)
#     -> Video Frame Buffer Write (YUYV8, 2 PPC) -> DDR -> /dev/videoN
#
# Video clock domain (CSI-2 RX video side, ISP, frame buffer): VCLK MHz.
# 4K30 needs ~181 MHz on average. Results (Vivado 2025.2, default strategy):
#   VCLK=250: WNS -0.012 ns (96 endpoints, SmartConnect -> CSI-2 RX, not the ISP)
#   VCLK=200: WNS +0.175 ns, met  <- default; openisp-m3.dts clk_video matches
# Run one build at a time (two in parallel failed in the PS IP synthesis).
#
# Usage:  [VCLK=200] [STRATEGY=Performance_Explore] vivado -mode batch -source build.tcl [-tclargs <out_dir>]

set script_dir [file dirname [file normalize [info script]]]
set out_dir    [expr {[llength $argv] > 0 ? [lindex $argv 0] : "$script_dir/../../../build/m3"}]
set proj_name  kv260_m3_isp
set jobs       [expr {[info exists ::env(JOBS)] ? $::env(JOBS) : 8}]

set brd [get_board_parts "*:kv260_som:*" -latest_file_version]
create_project -force $proj_name $out_dir -part [get_property PART_NAME [get_board_parts $brd]]
set_property board_part $brd [current_project]
set_property board_connections {som240_1_connector xilinx.com:kv260_carrier:som240_1_connector:1.3} [current_project]
set vclk       [expr {[info exists ::env(VCLK)] ? $::env(VCLK) : 200}]
add_files -fileset constrs_1 -norecurse $script_dir/../m0_raw_capture/pins.xdc

# ISP RTL (SystemVerilog) + Verilog shells used as module references
set rtl_dir [file normalize $script_dir/../../rtl]
add_files -norecurse [glob $rtl_dir/*/*.sv]
set_property file_type SystemVerilog [get_files -of_objects [get_filesets sources_1] *.sv]
add_files -norecurse [list $script_dir/isp_axil_bd.v $script_dir/isp_yuyv_bd.v]
update_compile_order -fileset sources_1

create_bd_design $proj_name

# ---------------------------------------------------------------- PS
set ps [create_bd_cell -type ip -vlnv xilinx.com:ip:zynq_ultra_ps_e ps]
apply_bd_automation -rule xilinx.com:bd_rule:zynq_ultra_ps_e -config {apply_board_preset "1"} $ps
set_property -dict [list \
  CONFIG.PSU__USE__M_AXI_GP0 {0} \
  CONFIG.PSU__USE__M_AXI_GP1 {0} \
  CONFIG.PSU__USE__M_AXI_GP2 {1} \
  CONFIG.PSU__MAXIGP2__DATA_WIDTH {32} \
  CONFIG.PSU__USE__S_AXI_GP2 {1} \
  CONFIG.PSU__SAXIGP2__DATA_WIDTH {128} \
  CONFIG.PSU__USE__IRQ0 {1} \
  CONFIG.PSU__FPGA_PL0_ENABLE {1} \
  CONFIG.PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ {100} \
  CONFIG.PSU__GPIO_EMIO__PERIPHERAL__ENABLE {1} \
  CONFIG.PSU__GPIO_EMIO__PERIPHERAL__IO {92} \
  CONFIG.PSU__TTC0__PERIPHERAL__ENABLE {1} \
  CONFIG.PSU__TTC0__WAVEOUT__ENABLE {1} \
  CONFIG.PSU__TTC0__WAVEOUT__IO {EMIO} \
] $ps

# ---------------------------------------------------------------- clocks / resets
set clk [create_bd_cell -type ip -vlnv xilinx.com:ip:clk_wiz clk]
set_property -dict [list \
  CONFIG.CLKOUT1_REQUESTED_OUT_FREQ {100.000} \
  CONFIG.CLKOUT2_USED {true} CONFIG.CLKOUT2_REQUESTED_OUT_FREQ {200.000} \
  CONFIG.CLKOUT3_USED {true} CONFIG.CLKOUT3_REQUESTED_OUT_FREQ $vclk \
  CONFIG.CLK_OUT1_PORT {clk_100M} CONFIG.CLK_OUT2_PORT {clk_200M} CONFIG.CLK_OUT3_PORT {clk_video} \
  CONFIG.RESET_TYPE {ACTIVE_LOW} CONFIG.RESET_PORT {resetn} \
] $clk
connect_bd_net [get_bd_pins ps/pl_clk0]     [get_bd_pins clk/clk_in1]
connect_bd_net [get_bd_pins ps/pl_resetn0]  [get_bd_pins clk/resetn]

foreach f {100M video} {
  set r [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset rst_$f]
  connect_bd_net [get_bd_pins clk/clk_$f]     [get_bd_pins rst_$f/slowest_sync_clk]
  connect_bd_net [get_bd_pins ps/pl_resetn0]  [get_bd_pins rst_$f/ext_reset_in]
  connect_bd_net [get_bd_pins clk/locked]     [get_bd_pins rst_$f/dcm_locked]
}
set c100 [get_bd_pins clk/clk_100M]
set cv [get_bd_pins clk/clk_video]   ;# video domain (VCLK)

# ---------------------------------------------------------------- control interconnect (HPM0_LPD, 0x8000_0000)
set sc [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect sc_ctrl]
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {5} CONFIG.NUM_CLKS {2}] $sc
connect_bd_intf_net [get_bd_intf_pins ps/M_AXI_HPM0_LPD] [get_bd_intf_pins sc_ctrl/S00_AXI]
connect_bd_net $c100 [get_bd_pins ps/maxihpm0_lpd_aclk] [get_bd_pins sc_ctrl/aclk]
connect_bd_net $cv [get_bd_pins sc_ctrl/aclk1]
connect_bd_net [get_bd_pins rst_100M/interconnect_aresetn] [get_bd_pins sc_ctrl/aresetn]

# ---------------------------------------------------------------- I2C (-> PCA9546 mux -> IAS/RPi sensors) and sensor GPIOs
set iic [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_iic iic]
set_property CONFIG.IIC_BOARD_INTERFACE {Custom} $iic
make_bd_intf_pins_external [get_bd_intf_pins iic/IIC]
set_property name iic [get_bd_intf_ports IIC_0]

# GPIO[0]=AP1302_RST_B  GPIO[1]=AP1302_STANDBY  GPIO[2]=IAS_XSHUTDOWN_B (J8)  GPIO[3]=RPi enable
set gpio [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio reset_gpio]
set_property -dict [list CONFIG.C_GPIO_WIDTH {4} CONFIG.C_ALL_OUTPUTS {1} CONFIG.C_DOUT_DEFAULT {0x00000000}] $gpio
make_bd_intf_pins_external [get_bd_intf_pins reset_gpio/GPIO]
set_property name GPIO [get_bd_intf_ports GPIO_0]

connect_bd_intf_net [get_bd_intf_pins sc_ctrl/M00_AXI] [get_bd_intf_pins iic/S_AXI]
connect_bd_intf_net [get_bd_intf_pins sc_ctrl/M01_AXI] [get_bd_intf_pins reset_gpio/S_AXI]
connect_bd_net $c100 [get_bd_pins iic/s_axi_aclk] [get_bd_pins reset_gpio/s_axi_aclk]
connect_bd_net [get_bd_pins rst_100M/peripheral_aresetn] [get_bd_pins iic/s_axi_aresetn] [get_bd_pins reset_gpio/s_axi_aresetn]

# ---------------------------------------------------------------- MIPI CSI-2 RX on IAS1 (J8)
set csi [create_bd_cell -type ip -vlnv xilinx.com:ip:mipi_csi2_rx_subsystem csi]
set_property -dict [list \
  CONFIG.CMN_NUM_LANES {4} \
  CONFIG.CMN_NUM_PIXELS {2} \
  CONFIG.CMN_PXL_FORMAT {RAW10} \
  CONFIG.CMN_VC {All} \
  CONFIG.CSI_BUF_DEPTH {4096} \
  CONFIG.C_CSI_FILTER_USERDATATYPE {true} \
  CONFIG.C_DPHY_LANES {4} \
  CONFIG.C_HS_LINE_RATE {880} \
  CONFIG.DPY_LINE_RATE {880} \
  CONFIG.C_HS_SETTLE_NS {146} \
  CONFIG.SupportLevel {1} \
  CONFIG.DPHYRX_BOARD_INTERFACE {som240_1_connector_mipi_csi_ias} \
  CONFIG.USE_BOARD_FLOW {true} \
] $csi
make_bd_intf_pins_external [get_bd_intf_pins csi/mipi_phy_if]
set_property name mipi_phy_if_ias [get_bd_intf_ports mipi_phy_if_0]
connect_bd_intf_net [get_bd_intf_pins sc_ctrl/M02_AXI] [get_bd_intf_pins csi/csirxss_s_axi]
connect_bd_net $c100 [get_bd_pins csi/lite_aclk]
connect_bd_net [get_bd_pins clk/clk_200M] [get_bd_pins csi/dphy_clk_200M]
connect_bd_net $cv [get_bd_pins csi/video_aclk]
connect_bd_net [get_bd_pins rst_100M/peripheral_aresetn] [get_bd_pins csi/lite_aresetn]
connect_bd_net [get_bd_pins rst_video/peripheral_aresetn] [get_bd_pins csi/video_aresetn]

# ---------------------------------------------------------------- CSI -> ISP input
# CSI 2PPC RAW10: pixel0 = tdata[9:0], pixel1 = tdata[19:10]; keep 3 bytes,
# TUSER[0] = SOF, drop TDEST (virtual channel)
set cvt [create_bd_cell -type ip -vlnv xilinx.com:ip:axis_subset_converter csi2isp]
connect_bd_intf_net [get_bd_intf_pins csi/video_out] [get_bd_intf_pins csi2isp/S_AXIS]
set vo [get_bd_intf_pins csi/video_out]
set_property -dict [list \
  CONFIG.S_TDATA_NUM_BYTES [get_property CONFIG.TDATA_NUM_BYTES $vo] \
  CONFIG.S_TUSER_WIDTH [get_property CONFIG.TUSER_WIDTH $vo] \
  CONFIG.S_TDEST_WIDTH [get_property CONFIG.TDEST_WIDTH $vo] \
  CONFIG.S_HAS_TLAST {1} \
  CONFIG.M_TDATA_NUM_BYTES {3} \
  CONFIG.TDATA_REMAP {4'b0000,tdata[19:0]} \
  CONFIG.M_TUSER_WIDTH {1} \
  CONFIG.TUSER_REMAP {tuser[0:0]} \
  CONFIG.M_TDEST_WIDTH {0} \
  CONFIG.M_HAS_TLAST {1} \
  CONFIG.TLAST_REMAP {tlast[0]} \
] $cvt

set fifo [create_bd_cell -type ip -vlnv xilinx.com:ip:axis_data_fifo fifo_cap]
set_property CONFIG.FIFO_DEPTH {2048} $fifo
connect_bd_intf_net [get_bd_intf_pins csi2isp/M_AXIS] [get_bd_intf_pins fifo_cap/S_AXIS]
connect_bd_net $cv [get_bd_pins csi2isp/aclk] [get_bd_pins fifo_cap/s_axis_aclk]
connect_bd_net [get_bd_pins rst_video/peripheral_aresetn] [get_bd_pins csi2isp/aresetn] [get_bd_pins fifo_cap/s_axis_aresetn]

# ---------------------------------------------------------------- Frame Buffer Write
set fb [create_bd_cell -type ip -vlnv xilinx.com:ip:v_frmbuf_wr fb_wr]
set_property -dict [list \
  CONFIG.SAMPLES_PER_CLOCK {2} \
  CONFIG.MAX_DATA_WIDTH {8} \
  CONFIG.MAX_COLS {4224} \
  CONFIG.MAX_ROWS {3136} \
  CONFIG.AXIMM_DATA_WIDTH {128} \
  CONFIG.C_M_AXI_MM_VIDEO_DATA_WIDTH {128} \
  CONFIG.HAS_RGB8 {0} \
  CONFIG.HAS_YUYV8 {1} \
] $fb
# ---------------------------------------------------------------- openISP + output packing
set isp [create_bd_cell -type module -reference isp_axil_bd isp]
set_property CONFIG.MAXW {3840} $isp
set yuyv [create_bd_cell -type module -reference isp_yuyv_bd yuyv]
connect_bd_intf_net [get_bd_intf_pins fifo_cap/M_AXIS] [get_bd_intf_pins isp/s_axis_video]
connect_bd_intf_net [get_bd_intf_pins isp/m_axis_video] [get_bd_intf_pins yuyv/s_axis_video]
connect_bd_intf_net [get_bd_intf_pins yuyv/m_axis_video] [get_bd_intf_pins fb_wr/s_axis_video]
connect_bd_intf_net [get_bd_intf_pins sc_ctrl/M04_AXI] [get_bd_intf_pins isp/s_axi_ctrl]
connect_bd_net $cv [get_bd_pins isp/aclk] [get_bd_pins yuyv/aclk]
connect_bd_net [get_bd_pins rst_video/peripheral_aresetn] [get_bd_pins isp/aresetn] [get_bd_pins yuyv/aresetn]
connect_bd_intf_net [get_bd_intf_pins sc_ctrl/M03_AXI] [get_bd_intf_pins fb_wr/s_axi_CTRL]
connect_bd_net $cv [get_bd_pins fb_wr/ap_clk]

# frmbuf reset is owned by the Linux driver through PS EMIO GPIO 0 (gpio line 78)
set sl [create_bd_cell -type ip -vlnv xilinx.com:ip:xlslice emio0]
set_property -dict [list CONFIG.DIN_WIDTH {92} CONFIG.DIN_FROM {0} CONFIG.DIN_TO {0}] $sl
connect_bd_net [get_bd_pins ps/emio_gpio_o] [get_bd_pins emio0/Din]
connect_bd_net [get_bd_pins emio0/Dout] [get_bd_pins fb_wr/ap_rst_n]

# video data to DDR via S_AXI_HP0_FPD
set sc_v [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect sc_video]
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {1}] $sc_v
connect_bd_intf_net [get_bd_intf_pins fb_wr/m_axi_mm_video] [get_bd_intf_pins sc_video/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins sc_video/M00_AXI] [get_bd_intf_pins ps/S_AXI_HP0_FPD]
connect_bd_net $cv [get_bd_pins sc_video/aclk] [get_bd_pins ps/saxihp0_fpd_aclk]
connect_bd_net [get_bd_pins rst_video/interconnect_aresetn] [get_bd_pins sc_video/aresetn]

# ---------------------------------------------------------------- fan (TTC0 PWM via EMIO, same as Kria platforms)
set fan [create_bd_cell -type ip -vlnv xilinx.com:ip:xlslice fan_slice]
set_property -dict [list CONFIG.DIN_WIDTH {3} CONFIG.DIN_FROM {2} CONFIG.DIN_TO {2}] $fan
connect_bd_net [get_bd_pins ps/emio_ttc0_wave_o] [get_bd_pins fan_slice/Din]
create_bd_port -dir O -from 0 -to 0 fan_en_b
connect_bd_net [get_bd_pins fan_slice/Dout] [get_bd_ports fan_en_b]

# ---------------------------------------------------------------- interrupts: pl_ps_irq0[0..3] = GIC SPI 89..92
set cc [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconcat irq]
set_property CONFIG.NUM_PORTS {4} $cc
connect_bd_net [get_bd_pins fb_wr/interrupt]       [get_bd_pins irq/In0]
connect_bd_net [get_bd_pins csi/csirxss_csi_irq]   [get_bd_pins irq/In1]
connect_bd_net [get_bd_pins iic/iic2intc_irpt]     [get_bd_pins irq/In2]
connect_bd_net [get_bd_pins isp/irq]             [get_bd_pins irq/In3]
connect_bd_net [get_bd_pins irq/dout] [get_bd_pins ps/pl_ps_irq0]

# ---------------------------------------------------------------- address map
foreach {seg base} {csi/csirxss_s_axi 0x80010000  fb_wr/s_axi_CTRL 0x80020000  iic/S_AXI 0x80030000  reset_gpio/S_AXI 0x80080000} {
  assign_bd_address -target_address_space /ps/Data -offset $base -range 64K [get_bd_addr_segs $seg/*]
}
assign_bd_address -target_address_space /ps/Data -offset 0x80040000 -range 256K [get_bd_addr_segs isp/s_axi_ctrl/*]
assign_bd_address   ;# remaining: frame buffer master -> DDR / OCM

validate_bd_design
save_bd_design

set bd [get_files $proj_name.bd]
make_wrapper -files $bd -top
add_files -norecurse [file join [file dirname $bd] hdl ${proj_name}_wrapper.v]
set_property top ${proj_name}_wrapper [current_fileset]

if {[info exists ::env(STRATEGY)]} { set_property strategy $::env(STRATEGY) [get_runs impl_1] }
launch_runs impl_1 -to_step write_bitstream -jobs $jobs
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] != "100%"} { error "implementation failed" }

open_run impl_1
report_utilization    -file $out_dir/utilization.rpt
report_timing_summary -file $out_dir/timing_summary.rpt
write_hw_platform -fixed -force -include_bit -file $out_dir/$proj_name.xsa
file copy -force [glob $out_dir/$proj_name.runs/impl_1/*.bit] $out_dir/$proj_name.bit
puts "INFO: WNS = [get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -setup]]"
puts "INFO: done -> $out_dir/$proj_name.bit"
