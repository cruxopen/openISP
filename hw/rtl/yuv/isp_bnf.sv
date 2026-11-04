// SPDX-License-Identifier: MIT
//
// isp_bnf - 5x5 bilateral filter on Y (hwmodel.blocks.bnf):
//   w = dw[i][j] * rw[level(|p - c|)], level: d >= t0 -> 0, >= t1 -> 1, >= t2 -> 2, else 3
//   out = div_round(sum w*p, sum w)   (c if sum w == 0)
module isp_bnf #(
  parameter int PPC = 2, MAXW = 3840, SB = 13, DWW = 12, RWW = 8
) (
  input  logic              clk, rst_n,
  input  logic [SB-1:0]     cfg_width, cfg_height,
  input  logic [25*DWW-1:0] dw,              // dw[i][j] at [(5i + j)*DWW +: DWW]
  input  logic [4*RWW-1:0]  rw,              // rw[l] at [l*RWW +: RWW]
  input  logic [3*8-1:0]    rthres,          // t0, t1, t2 (descending)
  input  logic [PPC*8-1:0]  s_tdata,
  input  logic              s_tvalid, s_tuser, s_tlast,
  output logic              s_tready,
  output logic [PPC*8-1:0]  m_tdata,
  output logic              m_tvalid, m_tuser, m_tlast,
  input  logic              m_tready
);
  logic [PPC*8-1:0] od;
  logic rdy;
  localparam int K = 5, NPOS = PPC + 4, WW = DWW + RWW, NW = WW + 8 + 5, DENW = WW + 5;
  localparam int LAT = 5 + 8;
  logic [K*NPOS*8-1:0] win;
  logic wv, wu, wl, wy, en;

  window_gen #(.DW(8), .PPC(PPC), .KH(K), .KW(K), .MAXW(MAXW), .SB(SB)) u_win (
    .clk, .rst_n, .cfg_width, .cfg_height,
    .s_tdata, .s_tvalid, .s_tuser, .s_tlast, .s_tready,
    .m_tdata(win), .m_tvalid(wv), .m_tuser(wu), .m_tlast(wl), .m_yodd(wy), .m_tready(rdy));

  assign en = 1'b1;                       // free-running pipeline
  pipe_out #(.L(LAT), .W(PPC*8), .SW(2)) u_pipe (
    .clk, .rst_n, .in_valid(wv), .in_side({wu, wl}), .in_ready(rdy),
    .od, .m_tdata, .m_tvalid, .m_side({m_tuser, m_tlast}), .m_tready);

  wire [7:0] t0 = rthres[7:0], t1 = rthres[15:8], t2 = rthres[23:16];

  for (genvar p = 0; p < PPC; p++) begin : g_px
    // s1: range weight
    logic [RWW-1:0] r1 [25];
    logic [7:0]     p1 [25], c1;
    always_ff @(posedge clk) begin
      if (en) begin
        logic [7:0] c;
        c = win[(2*NPOS + p + 2)*8 +: 8];
        for (int t = 0; t < 25; t++) begin
          logic [7:0] v, d;
          v = win[((t / 5)*NPOS + p + t % 5)*8 +: 8];
          d = (v > c) ? v - c : c - v;
          r1[t] <= (d >= t0) ? rw[0 +: RWW] : (d >= t1) ? rw[RWW +: RWW] :
                   (d >= t2) ? rw[2*RWW +: RWW] : rw[3*RWW +: RWW];
          p1[t] <= v;
        end
        c1 <= c;
      end
    end
    // s2: w = dw * r
    logic [WW-1:0] w2 [25];
    logic [7:0]    p2 [25], c2;
    always_ff @(posedge clk) begin
      if (en) begin
        for (int t = 0; t < 25; t++) w2[t] <= dw[t*DWW +: DWW] * r1[t];
        p2 <= p1; c2 <= c1;
      end
    end
    // s3: w * p
    logic [WW+7:0] pr3 [25];
    logic [WW-1:0] w3 [25];
    logic [7:0]    c3;
    always_ff @(posedge clk) begin
      if (en) begin
        for (int t = 0; t < 25; t++) pr3[t] <= w2[t] * p2[t];
        w3 <= w2; c3 <= c2;
      end
    end
    // s4: row sums, s5: totals
    logic [NW-1:0]   n4 [5], n5;
    logic [DENW-1:0] d4 [5], d5;
    logic [7:0]      c4, c5;
    always_ff @(posedge clk) begin
      if (en) begin
        for (int i = 0; i < 5; i++) begin
          n4[i] <= NW'(pr3[5*i]) + pr3[5*i+1] + pr3[5*i+2] + pr3[5*i+3] + pr3[5*i+4];
          d4[i] <= DENW'(w3[5*i]) + w3[5*i+1] + w3[5*i+2] + w3[5*i+3] + w3[5*i+4];
        end
        c4 <= c3;
        n5 <= n4[0] + n4[1] + n4[2] + n4[3] + n4[4];
        d5 <= d4[0] + d4[1] + d4[2] + d4[3] + d4[4];
        c5 <= c4;
      end
    end
    div_round_pipe #(.NW(NW), .DW(DENW), .QW(8)) u_div (
      .clk, .en, .num(n5), .den(d5), .alt(c5), .q(od[p*8 +: 8]));
  end
endmodule
