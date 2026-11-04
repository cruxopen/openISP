// pass-through stream + stats_bayer observer
// cfg: [7:0] cfa, [20:8] x0, [33:21] y0, [46:34] zw, [59:47] zh, [69:60] awb_sat, [79:70] awb_dark
`include "wrap_ports.svh"
  assign m_tdata = s_tdata; assign m_tvalid = s_tvalid; assign m_tuser = s_tuser;
  assign m_tlast = s_tlast; assign s_tready = m_tready;
  stats_bayer #(.SB(SB)) u (.clk, .rst_n, .cfg_height, .cfa(cfg[7:0]),
    .x0(cfg[20:8]), .y0(cfg[33:21]), .zw(cfg[46:34]), .zh(cfg[59:47]),
    .awb_sat(cfg[69:60]), .awb_dark(cfg[79:70]),
    .tdata(s_tdata), .fire(s_tvalid && s_tready), .tuser(s_tuser), .tlast(s_tlast),
    .rd_addr(rd_addr[11:0]), .rd_data);
endmodule
