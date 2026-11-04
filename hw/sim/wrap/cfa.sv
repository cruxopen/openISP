// cfg: [7:0] cfa
`include "wrap_ports.svh"
  assign rd_data = '0;
  isp_cfa #(.SB(SB)) u (.clk, .rst_n, .cfg_width, .cfg_height, .cfa(cfg[7:0]), .s_tdata, .s_tvalid, .s_tuser, .s_tlast, .s_tready, .m_tdata, .m_tvalid, .m_tuser, .m_tlast, .m_tready);
endmodule
