// cfg: [2:0] dshift; W addr data: LUT; AF window unused here
`include "wrap_ports.svh"
  assign rd_data = '0;
  logic [3*4*8-1:0] aw; logic af, au, al;
  isp_nlm #(.SB(SB)) u (.clk, .rst_n, .cfg_width, .cfg_height, .dshift(cfg[2:0]),
    .lut_we(wr_en), .lut_addr(wr_addr[7:0]), .lut_data(wr_data[7:0]), .s_tdata, .s_tvalid, .s_tuser, .s_tlast, .s_tready, .m_tdata, .m_tvalid, .m_tuser, .m_tlast, .m_tready,
    .af_win(aw), .af_fire(af), .af_user(au), .af_last(al));
endmodule
