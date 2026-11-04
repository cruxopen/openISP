// cfg: [7:0] cfa, [47:8] bl {b,gb,gr,r}, [57:48] alpha, [67:58] beta
`include "wrap_ports.svh"
  assign rd_data = '0;
  isp_blc u (.clk, .rst_n, .cfa(cfg[7:0]), .bl(cfg[47:8]), .alpha(cfg[57:48]), .beta(cfg[67:58]), .s_tdata, .s_tvalid, .s_tuser, .s_tlast, .s_tready, .m_tdata, .m_tvalid, .m_tuser, .m_tlast, .m_tready);
endmodule
