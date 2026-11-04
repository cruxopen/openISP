// cfg: [143:0] 9 x 16 coef, [209:144] 3 x 22 offsets
`include "wrap_ports.svh"
  assign rd_data = '0;
  isp_mat3 #(.IB(10), .OB(10)) u (.clk, .rst_n, .coef(cfg[143:0]), .offs(cfg[209:144]), .s_tdata, .s_tvalid, .s_tuser, .s_tlast, .s_tready, .m_tdata, .m_tvalid, .m_tuser, .m_tlast, .m_tready);
endmodule
