// SPDX-License-Identifier: MIT
// Verilog shell around isp_axil (SystemVerilog) for a block-design module reference.
module isp_axil_bd #(
  parameter MAXW = 3840
) (
  (* X_INTERFACE_PARAMETER = "ASSOCIATED_BUSIF s_axi_ctrl:s_axis_video:m_axis_video, ASSOCIATED_RESET aresetn" *)
  (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 aclk CLK" *)
  input  wire        aclk,
  (* X_INTERFACE_PARAMETER = "POLARITY ACTIVE_LOW" *)
  (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 aresetn RST" *)
  input  wire        aresetn,
  input  wire [17:0] s_axi_ctrl_awaddr,
  input  wire [2:0]  s_axi_ctrl_awprot,
  input  wire        s_axi_ctrl_awvalid,
  output wire        s_axi_ctrl_awready,
  input  wire [31:0] s_axi_ctrl_wdata,
  input  wire [3:0]  s_axi_ctrl_wstrb,
  input  wire        s_axi_ctrl_wvalid,
  output wire        s_axi_ctrl_wready,
  output wire [1:0]  s_axi_ctrl_bresp,
  output wire        s_axi_ctrl_bvalid,
  input  wire        s_axi_ctrl_bready,
  input  wire [17:0] s_axi_ctrl_araddr,
  input  wire [2:0]  s_axi_ctrl_arprot,
  input  wire        s_axi_ctrl_arvalid,
  output wire        s_axi_ctrl_arready,
  output wire [31:0] s_axi_ctrl_rdata,
  output wire [1:0]  s_axi_ctrl_rresp,
  output wire        s_axi_ctrl_rvalid,
  input  wire        s_axi_ctrl_rready,
  input  wire [23:0] s_axis_video_tdata,
  input  wire        s_axis_video_tvalid,
  input  wire [0:0]  s_axis_video_tuser,
  input  wire        s_axis_video_tlast,
  output wire        s_axis_video_tready,
  output wire [47:0] m_axis_video_tdata,
  output wire        m_axis_video_tvalid,
  output wire [0:0]  m_axis_video_tuser,
  output wire        m_axis_video_tlast,
  input  wire        m_axis_video_tready,
  (* X_INTERFACE_INFO = "xilinx.com:signal:interrupt:1.0 irq INTERRUPT" *)
  (* X_INTERFACE_PARAMETER = "SENSITIVITY LEVEL_HIGH" *)
  output wire        irq
);
  isp_axil #(.MAXW(MAXW)) u (
    .aclk(aclk), .aresetn(aresetn),
    .s_axi_ctrl_awaddr(s_axi_ctrl_awaddr), .s_axi_ctrl_awprot(s_axi_ctrl_awprot),
    .s_axi_ctrl_awvalid(s_axi_ctrl_awvalid), .s_axi_ctrl_awready(s_axi_ctrl_awready),
    .s_axi_ctrl_wdata(s_axi_ctrl_wdata), .s_axi_ctrl_wstrb(s_axi_ctrl_wstrb),
    .s_axi_ctrl_wvalid(s_axi_ctrl_wvalid), .s_axi_ctrl_wready(s_axi_ctrl_wready),
    .s_axi_ctrl_bresp(s_axi_ctrl_bresp), .s_axi_ctrl_bvalid(s_axi_ctrl_bvalid),
    .s_axi_ctrl_bready(s_axi_ctrl_bready),
    .s_axi_ctrl_araddr(s_axi_ctrl_araddr), .s_axi_ctrl_arprot(s_axi_ctrl_arprot),
    .s_axi_ctrl_arvalid(s_axi_ctrl_arvalid), .s_axi_ctrl_arready(s_axi_ctrl_arready),
    .s_axi_ctrl_rdata(s_axi_ctrl_rdata), .s_axi_ctrl_rresp(s_axi_ctrl_rresp),
    .s_axi_ctrl_rvalid(s_axi_ctrl_rvalid), .s_axi_ctrl_rready(s_axi_ctrl_rready),
    .s_axis_video_tdata(s_axis_video_tdata), .s_axis_video_tvalid(s_axis_video_tvalid),
    .s_axis_video_tuser(s_axis_video_tuser[0]), .s_axis_video_tlast(s_axis_video_tlast),
    .s_axis_video_tready(s_axis_video_tready),
    .m_axis_video_tdata(m_axis_video_tdata), .m_axis_video_tvalid(m_axis_video_tvalid),
    .m_axis_video_tuser(m_axis_video_tuser[0]), .m_axis_video_tlast(m_axis_video_tlast),
    .m_axis_video_tready(m_axis_video_tready), .irq(irq));
endmodule
