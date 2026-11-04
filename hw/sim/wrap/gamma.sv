// W <addr> <data>: LUT entry
`include "wrap_ports.svh"
  assign rd_data = '0;
  isp_gamma u (.clk, .rst_n, .lut_we(wr_en), .lut_addr(wr_addr[9:0]), .lut_data(wr_data[7:0]), .s_tdata, .s_tvalid, .s_tuser, .s_tlast, .s_tready, .m_tdata, .m_tvalid, .m_tuser, .m_tlast, .m_tready);
endmodule
