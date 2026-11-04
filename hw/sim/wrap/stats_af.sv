// window_gen 3x3 (Y, 8 bit) + stats_af observer; the window stream is consumed
// cfg: [12:0] x0, [25:13] y0, [38:26] zw, [51:39] zh, [60:52] coring
`include "wrap_ports.svh"
  logic [3*4*8-1:0] win;
  logic wv, wu, wl, wy;
  window_gen #(.DW(8), .PPC(2), .KH(3), .KW(3), .SB(SB)) u_win (
    .clk, .rst_n, .cfg_width, .cfg_height, .s_tdata, .s_tvalid, .s_tuser, .s_tlast, .s_tready,
    .m_tdata(win), .m_tvalid(wv), .m_tuser(wu), .m_tlast(wl), .m_yodd(wy), .m_tready(m_tready));
  assign m_tdata = win[1*32 + 8 +: 16]; assign m_tvalid = wv; assign m_tuser = wu; assign m_tlast = wl;
  stats_af #(.SB(SB)) u (.clk, .rst_n, .cfg_height,
    .x0(cfg[12:0]), .y0(cfg[25:13]), .zw(cfg[38:26]), .zh(cfg[51:39]), .coring(cfg[60:52]),
    .win3(win), .fire(wv && m_tready), .tuser(wu), .tlast(wl), .rd_addr(rd_addr[11:0]), .rd_data);
endmodule
