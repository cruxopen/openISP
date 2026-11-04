// SPDX-License-Identifier: MIT
//
// isp_dpc - dead pixel correction (hwmodel.blocks.dpc), 5x5 window, PPC pixels/beat.
//   p0 is replaced when |p - p0| > thres for all 8 same-colour neighbours
//   (distance 2).  mode 0 = gradient (direction of the smallest second
//   difference, first minimum wins), mode 1 = mean of the 4 axial neighbours.
module isp_dpc #(
  parameter int DW = 10, PPC = 2, MAXW = 3840, SB = 13
) (
  input  logic              clk, rst_n,
  input  logic [SB-1:0]     cfg_width, cfg_height,
  input  logic [DW-1:0]     thres,
  input  logic              mode_mean,
  input  logic [PPC*DW-1:0] s_tdata,
  input  logic              s_tvalid, s_tuser, s_tlast,
  output logic              s_tready,
  output logic [PPC*DW-1:0] m_tdata,
  output logic              m_tvalid, m_tuser, m_tlast,
  input  logic              m_tready
);
  logic [PPC*DW-1:0] od;
  logic rdy;
  localparam int K = 5, NPOS = PPC + 4, LAT = 3;
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
    function automatic logic signed [DW+2:0] px(int k, int j);
      return $signed({3'b0, win[(k*NPOS + p + j)*DW +: DW]});
    endfunction

    logic signed [DW+2:0] c, n[8], g[4], cs[4];
    logic signed [DW+3:0] msum;
    logic                 dead_c;
    always_comb begin
      c = px(2, 2);
      n[0] = px(0, 0); n[1] = px(0, 2); n[2] = px(0, 4);
      n[3] = px(2, 0);                  n[4] = px(2, 4);
      n[5] = px(4, 0); n[6] = px(4, 2); n[7] = px(4, 4);
      dead_c = 1'b1;
      for (int i = 0; i < 8; i++) begin
        logic signed [DW+2:0] d;
        d = n[i] - c;
        if (d < 0) d = -d;
        if (!(d > $signed({3'b0, thres}))) dead_c = 1'b0;
      end
      cs[0] = n[1] + n[6];  cs[1] = n[3] + n[4];        // vertical, horizontal
      cs[2] = n[0] + n[7];  cs[3] = n[2] + n[5];        // diagonal \ and /
      for (int i = 0; i < 4; i++) begin
        g[i] = 2 * c - cs[i];
        if (g[i] < 0) g[i] = -g[i];
      end
      msum = cs[0] + cs[1] + 2;
    end

    // stage 1
    logic [DW-1:0]   c1;
    logic            dead1;
    logic [DW+1:0]   g1[4];
    logic [DW:0]     cs1[4];
    logic [DW+1:0]   mean1;
    always_ff @(posedge clk) begin
      if (en) begin
        c1    <= c[DW-1:0];
        dead1 <= dead_c;
        for (int i = 0; i < 4; i++) begin
          g1[i]  <= g[i][DW+1:0];
          cs1[i] <= cs[i][DW:0];
        end
        mean1 <= msum[DW+3:2];                         // (p2 + p7 + p4 + p5 + 2) >> 2
      end
    end

    // stage 2: direction of the smallest gradient (first minimum wins)
    logic [1:0]    sel2;
    logic [DW:0]   cs2[4];
    logic [DW+1:0] mean2;
    logic [DW-1:0] c2;
    logic          dead2;
    always_ff @(posedge clk) begin
      if (en) begin
        logic [DW+1:0] best;
        logic [1:0]    sel;
        best = g1[0]; sel = 2'd0;
        for (int i = 1; i < 4; i++)
          if (g1[i] < best) begin best = g1[i]; sel = 2'(i); end
        sel2 <= sel; cs2 <= cs1; mean2 <= mean1; c2 <= c1; dead2 <= dead1;
      end
    end
    // stage 3: replacement
    always_ff @(posedge clk) begin
      if (en) begin
        logic [DW-1:0] fix;
        fix = mode_mean ? mean2[DW-1:0] : DW'((cs2[sel2] + 1) >> 1);
        od[p*DW +: DW] <= dead2 ? fix : c2;
      end
    end
  end
endmodule
