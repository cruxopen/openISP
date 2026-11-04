// SPDX-License-Identifier: MIT
//
// isp_nlm - non-local means on Y (hwmodel.blocks.nlm), 7x7 window:
// 3x3 patches, 5x5 search (24 neighbours), SAD distance, weight LUT
// (256 x 8, index min(SAD >> dshift, 255)), centre weight = max neighbour
// weight, out = div_round(sum w*p, sum w).
// Absolute differences are shared by the PPC pixels through column sums.
// af_*: the centre 3 x (PPC+2) of the window, for stats_af.
module isp_nlm #(
  parameter int PPC = 2, MAXW = 3840, SB = 13
) (
  input  logic                    clk, rst_n,
  input  logic [SB-1:0]           cfg_width, cfg_height,
  input  logic [2:0]              dshift,
  input  logic                    lut_we,
  input  logic [7:0]              lut_addr, lut_data,
  input  logic [PPC*8-1:0]        s_tdata,
  input  logic                    s_tvalid, s_tuser, s_tlast,
  output logic                    s_tready,
  output logic [PPC*8-1:0]        m_tdata,
  output logic                    m_tvalid, m_tuser, m_tlast,
  input  logic                    m_tready,
  output logic [3*(PPC+2)*8-1:0]  af_win,
  output logic                    af_fire, af_user, af_last
);
  logic [PPC*8-1:0] od;
  logic rdy;
  localparam int K = 7, NPOS = PPC + 6, NN = 24, NC = PPC + 2;
  localparam int LAT = 9 + 8;
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

  for (genvar k = 0; k < 3; k++) begin : g_afr
    for (genvar i = 0; i < NC; i++) begin : g_afc
      assign af_win[(k*NC + i)*8 +: 8] = win[((k+2)*NPOS + i + 2)*8 +: 8];
    end
  end
  assign af_fire = wv && rdy;
  assign af_user = wu;
  assign af_last = wl;

  function automatic logic [7:0] px(int r, int c);
    return win[(r*NPOS + c)*8 +: 8];
  endfunction
  // neighbour n -> (dy, dx), raster order without (0, 0).  Constant tables
  // (not functions: xsim 2025.2 mis-evaluates two different automatic
  // function calls in one expression).
  localparam int OY [NN] = '{-2, -2, -2, -2, -2, -1, -1, -1, -1, -1, 0, 0, 0, 0,
                             1, 1, 1, 1, 1, 2, 2, 2, 2, 2};
  localparam int OX [NN] = '{-2, -1, 0, 1, 2, -2, -1, 0, 1, 2, -2, -1, 1, 2,
                             -2, -1, 0, 1, 2, -2, -1, 0, 1, 2};

  // s1: |patch(d) - patch(0)| on rows 2..4, columns 2 .. PPC+3
  logic [7:0] ad1 [NN][3][NC];
  logic [7:0] nb1 [PPC][NN], c1 [PPC];
  always_ff @(posedge clk) begin
    if (en) begin
      for (int n = 0; n < NN; n++)
        for (int r = 0; r < 3; r++)
          for (int c = 0; c < NC; c++) begin
            logic [7:0] a, b;
            a = px(2 + r + OY[n], 2 + c + OX[n]);
            b = px(2 + r, 2 + c);
            ad1[n][r][c] <= (a > b) ? a - b : b - a;
          end
      for (int p = 0; p < PPC; p++) begin
        c1[p] <= px(3, 3 + p);
        for (int n = 0; n < NN; n++) nb1[p][n] <= px(3 + OY[n], 3 + p + OX[n]);
      end
    end
  end

  // s2: column sums
  logic [9:0] cs2 [NN][NC];
  logic [7:0] nb2 [PPC][NN], c2 [PPC];
  always_ff @(posedge clk) begin
    if (en) begin
      for (int n = 0; n < NN; n++)
        for (int c = 0; c < NC; c++) cs2[n][c] <= ad1[n][0][c] + ad1[n][1][c] + ad1[n][2][c];
      nb2 <= nb1; c2 <= c1;
    end
  end

  // s3: SAD -> LUT index
  logic [PPC*NN*8-1:0] idx3, wt4;
  logic [7:0] nb3 [PPC][NN], c3 [PPC];
  always_ff @(posedge clk) begin
    if (en) begin
      for (int p = 0; p < PPC; p++)
        for (int n = 0; n < NN; n++) begin
          logic [11:0] sad, sh;
          sad = cs2[n][p] + cs2[n][p+1] + cs2[n][p+2];
          sh  = sad >> dshift;
          idx3[(p*NN + n)*8 +: 8] <= (sh > 255) ? 8'd255 : sh[7:0];
        end
      nb3 <= nb2; c3 <= c2;
    end
  end

  // s4: weights (registered LUT read)
  lut_mem #(.AW(8), .DW(8), .NRD(PPC*NN), .STYLE("distributed")) u_lut (
    .clk, .en, .we(lut_we), .waddr(lut_addr), .wdata(lut_data), .raddr(idx3), .rdata(wt4));
  logic [7:0] nb4 [PPC][NN], c4 [PPC];
  always_ff @(posedge clk) if (en) begin nb4 <= nb3; c4 <= c3; end

  for (genvar p = 0; p < PPC; p++) begin : g_px
    function automatic logic [7:0] wt(int n); return wt4[(p*NN + n)*8 +: 8]; endfunction

    // s5: products, group max / sum of 4
    logic [15:0] pr5 [NN];
    logic [7:0]  m5 [6];
    logic [9:0]  d5 [6];
    logic [7:0]  c5;
    always_ff @(posedge clk) begin
      if (en) begin
        for (int n = 0; n < NN; n++) pr5[n] <= wt(n) * nb4[p][n];
        for (int g = 0; g < 6; g++) begin
          logic [7:0] m;
          m = wt(4*g);
          for (int j = 1; j < 4; j++) if (wt(4*g + j) > m) m = wt(4*g + j);
          m5[g] <= m;
          d5[g] <= wt(4*g) + wt(4*g+1) + wt(4*g+2) + wt(4*g+3);
        end
        c5 <= c4[p];
      end
    end

    // s6
    logic [17:0] ps6 [6];
    logic [7:0]  m6 [3];
    logic [12:0] d6;
    logic [7:0]  c6;
    always_ff @(posedge clk) begin
      if (en) begin
        for (int g = 0; g < 6; g++) ps6[g] <= pr5[4*g] + pr5[4*g+1] + pr5[4*g+2] + pr5[4*g+3];
        for (int h = 0; h < 3; h++) m6[h] <= (m5[2*h] > m5[2*h+1]) ? m5[2*h] : m5[2*h+1];
        d6 <= d5[0] + d5[1] + d5[2] + d5[3] + d5[4] + d5[5];
        c6 <= c5;
      end
    end

    // s7
    logic [20:0] n7;
    logic [7:0]  m7, c7;
    logic [12:0] d7;
    always_ff @(posedge clk) begin
      if (en) begin
        logic [7:0] m;
        n7 <= ps6[0] + ps6[1] + ps6[2] + ps6[3] + ps6[4] + ps6[5];
        m = (m6[0] > m6[1]) ? m6[0] : m6[1];
        m7 <= (m6[2] > m) ? m6[2] : m;
        d7 <= d6; c7 <= c6;
      end
    end

    // s8, s9: centre weight
    logic [20:0] n8, n9;
    logic [15:0] pc8;
    logic [12:0] d8, d9;
    logic [7:0]  c8, c9;
    always_ff @(posedge clk) begin
      if (en) begin
        pc8 <= m7 * c7; n8 <= n7; d8 <= d7 + m7; c8 <= c7;
        n9 <= n8 + pc8; d9 <= d8; c9 <= c8;
      end
    end

    div_round_pipe #(.NW(21), .DW(13), .QW(8)) u_div (
      .clk, .en, .num(n9), .den(d9), .alt(c9), .q(od[p*8 +: 8]));
  end
endmodule
