// SPDX-License-Identifier: MIT
//
// isp_axil - isp_top with an AXI4-Lite control port, for the Vivado block
// design (add as an RTL module reference).
//   s_axi_ctrl   : AXI4-Lite, 18-bit byte address (64 KiW), register map in
//                  hwmodel/regmap.py / sw/include/openisp_regs.h
//   s_axis_video : RAW10 2 pixels/beat {p1, p0} (20 bit, padded to 24)
//   m_axis_video : YUV444 2 pixels/beat {V1 U1 Y1 V0 U0 Y0} (48 bit)
//   irq          : level, active high (IRQ_STATUS & IRQ_ENABLE)
module isp_axil #(
  parameter int MAXW = 3840
) (
  (* X_INTERFACE_PARAMETER = "ASSOCIATED_BUSIF s_axi_ctrl:s_axis_video:m_axis_video, ASSOCIATED_RESET aresetn" *)
  (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 aclk CLK" *)
  input  logic        aclk,
  (* X_INTERFACE_PARAMETER = "POLARITY ACTIVE_LOW" *)
  (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 aresetn RST" *)
  input  logic        aresetn,

  input  logic [17:0] s_axi_ctrl_awaddr,
  input  logic [2:0]  s_axi_ctrl_awprot,
  input  logic        s_axi_ctrl_awvalid,
  output logic        s_axi_ctrl_awready,
  input  logic [31:0] s_axi_ctrl_wdata,
  input  logic [3:0]  s_axi_ctrl_wstrb,
  input  logic        s_axi_ctrl_wvalid,
  output logic        s_axi_ctrl_wready,
  output logic [1:0]  s_axi_ctrl_bresp,
  output logic        s_axi_ctrl_bvalid,
  input  logic        s_axi_ctrl_bready,
  input  logic [17:0] s_axi_ctrl_araddr,
  input  logic [2:0]  s_axi_ctrl_arprot,
  input  logic        s_axi_ctrl_arvalid,
  output logic        s_axi_ctrl_arready,
  output logic [31:0] s_axi_ctrl_rdata,
  output logic [1:0]  s_axi_ctrl_rresp,
  output logic        s_axi_ctrl_rvalid,
  input  logic        s_axi_ctrl_rready,

  input  logic [23:0] s_axis_video_tdata,
  input  logic        s_axis_video_tvalid,
  input  logic        s_axis_video_tuser,
  input  logic        s_axis_video_tlast,
  output logic        s_axis_video_tready,

  output logic [47:0] m_axis_video_tdata,
  output logic        m_axis_video_tvalid,
  output logic        m_axis_video_tuser,
  output logic        m_axis_video_tlast,
  input  logic        m_axis_video_tready,

  (* X_INTERFACE_INFO = "xilinx.com:signal:interrupt:1.0 irq INTERRUPT" *)
  (* X_INTERFACE_PARAMETER = "SENSITIVITY LEVEL_HIGH" *)
  output logic        irq
);
  logic        wr_en;
  logic [15:0] wr_addr, rd_addr;
  logic [31:0] wr_data, rd_data;

  // register the reset (fan-out to every block)
  logic rst_n;
  always_ff @(posedge aclk) rst_n <= aresetn;

  axil_regbus #(.AW(18), .RL(2)) u_bus (
    .clk(aclk), .rst_n,
    .s_axi_awaddr(s_axi_ctrl_awaddr), .s_axi_awvalid(s_axi_ctrl_awvalid), .s_axi_awready(s_axi_ctrl_awready),
    .s_axi_wdata(s_axi_ctrl_wdata), .s_axi_wstrb(s_axi_ctrl_wstrb), .s_axi_wvalid(s_axi_ctrl_wvalid),
    .s_axi_wready(s_axi_ctrl_wready), .s_axi_bresp(s_axi_ctrl_bresp), .s_axi_bvalid(s_axi_ctrl_bvalid),
    .s_axi_bready(s_axi_ctrl_bready), .s_axi_araddr(s_axi_ctrl_araddr), .s_axi_arvalid(s_axi_ctrl_arvalid),
    .s_axi_arready(s_axi_ctrl_arready), .s_axi_rdata(s_axi_ctrl_rdata), .s_axi_rresp(s_axi_ctrl_rresp),
    .s_axi_rvalid(s_axi_ctrl_rvalid), .s_axi_rready(s_axi_ctrl_rready),
    .wr_en, .wr_addr, .wr_data, .rd_addr, .rd_data);

  isp_top #(.MAXW(MAXW)) u_isp (
    .clk(aclk), .rst_n, .wr_en, .wr_addr, .wr_data, .rd_addr, .rd_data,
    .s_tdata(s_axis_video_tdata[19:0]), .s_tvalid(s_axis_video_tvalid), .s_tuser(s_axis_video_tuser),
    .s_tlast(s_axis_video_tlast), .s_tready(s_axis_video_tready),
    .m_tdata(m_axis_video_tdata), .m_tvalid(m_axis_video_tvalid), .m_tuser(m_axis_video_tuser),
    .m_tlast(m_axis_video_tlast), .m_tready(m_axis_video_tready), .irq);
endmodule
