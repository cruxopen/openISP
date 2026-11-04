// cfg: [9:0] thres, [10] mode_mean
`include "wrap_ports.svh"
  assign rd_data = '0;
  isp_dpc #(.SB(SB)) u (.clk, .rst_n, .cfg_width, .cfg_height, .thres(cfg[9:0]), .mode_mean(cfg[10]),
    .s_tdata, .s_tvalid, .s_tuser, .s_tlast, .s_tready, .m_tdata, .m_tvalid, .m_tuser, .m_tlast, .m_tready);
endmodule
