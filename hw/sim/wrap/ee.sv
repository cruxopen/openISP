// cfg: [119:0] filt 15x8, [139:120] gain 2x10, [171:140] thr 2x16, [191:172] emclip 2x10
`include "wrap_ports.svh"
  assign rd_data = '0;
  isp_ee #(.SB(SB)) u (.clk, .rst_n, .cfg_width, .cfg_height, .filt(cfg[119:0]), .gain(cfg[139:120]),
    .thr(cfg[171:140]), .emclip(cfg[191:172]), .s_tdata, .s_tvalid, .s_tuser, .s_tlast, .s_tready, .m_tdata, .m_tvalid, .m_tuser, .m_tlast, .m_tready);
endmodule
