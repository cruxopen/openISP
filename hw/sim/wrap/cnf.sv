// cfg: [7:0] cfa, [17:8] thres, [31:18] gain_r, [45:32] gain_b
`include "wrap_ports.svh"
  assign rd_data = '0;
  isp_cnf #(.SB(SB)) u (.clk, .rst_n, .cfg_width, .cfg_height, .cfa(cfg[7:0]), .thres(cfg[17:8]),
    .gain_r(cfg[31:18]), .gain_b(cfg[45:32]), .s_tdata, .s_tvalid, .s_tuser, .s_tlast, .s_tready, .m_tdata, .m_tvalid, .m_tuser, .m_tlast, .m_tready);
endmodule
