// cfg: [299:0] dw 25x12, [331:300] rw 4x8, [355:332] rthres 3x8
`include "wrap_ports.svh"
  assign rd_data = '0;
  isp_bnf #(.SB(SB)) u (.clk, .rst_n, .cfg_width, .cfg_height, .dw(cfg[299:0]), .rw(cfg[331:300]),
    .rthres(cfg[355:332]), .s_tdata, .s_tvalid, .s_tuser, .s_tlast, .s_tready, .m_tdata, .m_tvalid, .m_tuser, .m_tlast, .m_tready);
endmodule
