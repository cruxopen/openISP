// cfg: [10:0] e0 [21:11] e1 [30:22] gain [40:31] sin [50:41] cos [60:51] sat [69:61] bright [77:70] contrast
`include "wrap_ports.svh"
  assign rd_data = '0;
  isp_yuvpost u (.clk, .rst_n, .fcs_e0(cfg[10:0]), .fcs_e1(cfg[21:11]), .fcs_gain(cfg[30:22]),
    .hsc_sin(cfg[40:31]), .hsc_cos(cfg[50:41]), .hsc_sat(cfg[60:51]), .bright(cfg[69:61]),
    .contrast(cfg[77:70]), .s_tdata, .s_tvalid, .s_tuser, .s_tlast, .s_tready, .m_tdata, .m_tvalid, .m_tuser, .m_tlast, .m_tready);
endmodule
