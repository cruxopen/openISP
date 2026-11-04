// cfg: [7:0] cfa, [63:8] gains {b,gb,gr,r} x 14
`include "wrap_ports.svh"
  assign rd_data = '0;
  isp_awb u (.clk, .rst_n, .cfa(cfg[7:0]), .gain(cfg[63:8]), .s_tdata, .s_tvalid, .s_tuser, .s_tlast, .s_tready, .m_tdata, .m_tvalid, .m_tuser, .m_tlast, .m_tready);
endmodule
