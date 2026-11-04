// SPDX-License-Identifier: MIT
// Verilog shell around isp_yuyv (SystemVerilog) for a block-design module reference.
module isp_yuyv_bd (
  (* X_INTERFACE_PARAMETER = "ASSOCIATED_BUSIF s_axis_video:m_axis_video, ASSOCIATED_RESET aresetn" *)
  (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 aclk CLK" *)
  input  wire        aclk,
  (* X_INTERFACE_PARAMETER = "POLARITY ACTIVE_LOW" *)
  (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 aresetn RST" *)
  input  wire        aresetn,
  input  wire [47:0] s_axis_video_tdata,
  input  wire        s_axis_video_tvalid,
  input  wire [0:0]  s_axis_video_tuser,
  input  wire        s_axis_video_tlast,
  output wire        s_axis_video_tready,
  output wire [47:0] m_axis_video_tdata,
  output wire        m_axis_video_tvalid,
  output wire [0:0]  m_axis_video_tuser,
  output wire        m_axis_video_tlast,
  input  wire        m_axis_video_tready
);
  isp_yuyv u (
    .aclk(aclk), .aresetn(aresetn),
    .s_axis_video_tdata(s_axis_video_tdata), .s_axis_video_tvalid(s_axis_video_tvalid),
    .s_axis_video_tuser(s_axis_video_tuser[0]), .s_axis_video_tlast(s_axis_video_tlast),
    .s_axis_video_tready(s_axis_video_tready),
    .m_axis_video_tdata(m_axis_video_tdata), .m_axis_video_tvalid(m_axis_video_tvalid),
    .m_axis_video_tuser(m_axis_video_tuser[0]), .m_axis_video_tlast(m_axis_video_tlast),
    .m_axis_video_tready(m_axis_video_tready));
endmodule
