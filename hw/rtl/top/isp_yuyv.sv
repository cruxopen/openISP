// SPDX-License-Identifier: MIT
//
// isp_yuyv - ISP output packing for the frame buffer (Vivado block design
// module reference): YUV 4:4:4, 2 pixels/beat -> YUV 4:2:2 (YUYV).
//   s_axis_video : {V1 U1 Y1 V0 U0 Y0} (48 bit, isp_axil m_axis_video)
//   m_axis_video : {16'b0, V, Y1, U, Y0}, U = (U0+U1+1)>>1, V = (V0+V1+1)>>1
// The Xilinx frame buffer (YUYV8, 2 PPC) takes 2 components per pixel from
// tdata[31:0] and writes them unchanged -> memory Y0 U Y1 V.
// Golden model: hwmodel.blocks.yuyv.  One register stage, full throughput.
module isp_yuyv (
  (* X_INTERFACE_PARAMETER = "ASSOCIATED_BUSIF s_axis_video:m_axis_video, ASSOCIATED_RESET aresetn" *)
  (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 aclk CLK" *)
  input  logic        aclk,
  (* X_INTERFACE_PARAMETER = "POLARITY ACTIVE_LOW" *)
  (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 aresetn RST" *)
  input  logic        aresetn,
  input  logic [47:0] s_axis_video_tdata,
  input  logic        s_axis_video_tvalid,
  input  logic        s_axis_video_tuser,
  input  logic        s_axis_video_tlast,
  output logic        s_axis_video_tready,
  output logic [47:0] m_axis_video_tdata,
  output logic        m_axis_video_tvalid,
  output logic        m_axis_video_tuser,
  output logic        m_axis_video_tlast,
  input  logic        m_axis_video_tready
);
  logic [7:0] y0, u0, v0, y1, u1, v1;
  assign {v1, u1, y1, v0, u0, y0} = s_axis_video_tdata;
  wire [8:0] us = u0 + u1 + 9'd1, vs = v0 + v1 + 9'd1;

  assign s_axis_video_tready = !m_axis_video_tvalid || m_axis_video_tready;
  always_ff @(posedge aclk) begin
    if (!aresetn) begin
      m_axis_video_tvalid <= 1'b0;
    end else if (s_axis_video_tready) begin
      m_axis_video_tvalid <= s_axis_video_tvalid;
      m_axis_video_tdata  <= {16'd0, vs[8:1], y1, us[8:1], y0};
      m_axis_video_tuser  <= s_axis_video_tuser;
      m_axis_video_tlast  <= s_axis_video_tlast;
    end
  end
endmodule
