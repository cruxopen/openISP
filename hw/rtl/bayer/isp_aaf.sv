// SPDX-License-Identifier: MIT
//
// isp_aaf - anti-aliasing filter (hwmodel.blocks.aaf): 3x3 same-colour
// average, centre weight 8/16:  (8*c + sum of 8 neighbours at distance 2 + 8) >> 4
module isp_aaf #(
  parameter int DW = 10, PPC = 2, MAXW = 3840, SB = 13
) (
  input  logic              clk, rst_n,
  input  logic [SB-1:0]     cfg_width, cfg_height,
  input  logic [PPC*DW-1:0] s_tdata,
  input  logic              s_tvalid, s_tuser, s_tlast,
  output logic              s_tready,
  output logic [PPC*DW-1:0] m_tdata,
  output logic              m_tvalid, m_tuser, m_tlast,
  input  logic              m_tready
);
  logic [PPC*DW-1:0] od;
  logic rdy;
  localparam int K = 5, NPOS = PPC + 4, LAT = 2;
  logic [K*NPOS*DW-1:0] win;
  logic wv, wu, wl, wy, en;

  window_gen #(.DW(DW), .PPC(PPC), .KH(K), .KW(K), .MAXW(MAXW), .SB(SB)) u_win (
    .clk, .rst_n, .cfg_width, .cfg_height,
    .s_tdata, .s_tvalid, .s_tuser, .s_tlast, .s_tready,
    .m_tdata(win), .m_tvalid(wv), .m_tuser(wu), .m_tlast(wl), .m_yodd(wy), .m_tready(rdy));

  assign en = 1'b1;                       // free-running pipeline
  pipe_out #(.L(LAT), .W(PPC*DW), .SW(2)) u_pipe (
    .clk, .rst_n, .in_valid(wv), .in_side({wu, wl}), .in_ready(rdy),
    .od, .m_tdata, .m_tvalid, .m_side({m_tuser, m_tlast}), .m_tready);

  for (genvar p = 0; p < PPC; p++) begin : g_px
    function automatic logic [DW+3:0] px(int k, int j);
      return {4'b0, win[(k*NPOS + p + j)*DW +: DW]};
    endfunction
    logic [DW+3:0] r0, r1, c1;
    always_ff @(posedge clk) begin
      if (en) begin                                     // stage 1: row sums
        r0 <= px(0, 0) + px(0, 2) + px(0, 4) + px(4, 0);
        r1 <= px(4, 2) + px(4, 4) + px(2, 0) + px(2, 4);
        c1 <= px(2, 2) << 3;
      end
    end
    logic [DW+3:0] s;
    assign s = r0 + r1 + c1 + 8;
    always_ff @(posedge clk)
      if (en) od[p*DW +: DW] <= s[DW+3:4];        // stage 2
  end
endmodule
