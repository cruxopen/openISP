// output packing YUV444 -> YUYV (no configuration)
`include "wrap_ports.svh"
  assign rd_data = '0;
  isp_yuyv u (.aclk(clk), .aresetn(rst_n),
    .s_axis_video_tdata(s_tdata), .s_axis_video_tvalid(s_tvalid), .s_axis_video_tuser(s_tuser),
    .s_axis_video_tlast(s_tlast), .s_axis_video_tready(s_tready),
    .m_axis_video_tdata(m_tdata), .m_axis_video_tvalid(m_tvalid), .m_axis_video_tuser(m_tuser),
    .m_axis_video_tlast(m_tlast), .m_axis_video_tready(m_tready));
endmodule
