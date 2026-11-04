// SPDX-License-Identifier: MIT
//
// isp_cnf - chroma noise filter on R/B sites (hwmodel.blocks.cnf), 9x9 window.
//   C1 = centre colour (25 samples), C2 = other chroma (16), G (40).
//   Column sums are shared by the PPC output pixels.
module isp_cnf #(
  parameter int DW = 10, PPC = 2, MAXW = 3840, SB = 13, GW = 14
) (
  input  logic              clk, rst_n,
  input  logic [SB-1:0]     cfg_width, cfg_height,
  input  logic [7:0]        cfa,
  input  logic [DW-1:0]     thres,
  input  logic [GW-1:0]     gain_r, gain_b,     // AWB gains Q10 (damping)
  input  logic [PPC*DW-1:0] s_tdata,
  input  logic              s_tvalid, s_tuser, s_tlast,
  output logic              s_tready,
  output logic [PPC*DW-1:0] m_tdata,
  output logic              m_tvalid, m_tuser, m_tlast,
  input  logic              m_tready
);
  logic [PPC*DW-1:0] od;
  logic rdy;
  localparam int K = 9, NPOS = PPC + 8, LAT = 11;
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

  function automatic logic [8:0] damp_q8(logic [GW-1:0] g);
    return (g <= 1024) ? 9'd256 : (g <= 1229) ? 9'd128 : 9'd77;
  endfunction
  function automatic logic [8:0] fade1(logic [DW-1:0] v);  // thresholds 120 .. 1000
    if (v <= 120) return 256; if (v <= 200) return 230; if (v <= 280) return 205;
    if (v <= 400) return 179; if (v <= 600) return 154; if (v <= 800) return 77;
    if (v <= 1000) return 26; return 0;
  endfunction
  function automatic logic [8:0] fade2(logic [DW-1:0] v);  // thresholds 120 .. 800
    if (v <= 120) return 256; if (v <= 200) return 230; if (v <= 280) return 205;
    if (v <= 400) return 154; if (v <= 600) return 128; if (v <= 800) return 77;
    return 0;
  endfunction

  // stage 1: column sums of even rows (5 samples) and odd rows (4 samples)
  logic [DW+2:0] ce [NPOS], co [NPOS];
  logic [DW-1:0] c1 [PPC];
  logic          y1;
  always_ff @(posedge clk) begin
    if (en) begin
      for (int i = 0; i < NPOS; i++) begin
        logic [DW+2:0] se, so;
        se = '0; so = '0;
        for (int k = 0; k < K; k += 2) se += win[(k*NPOS + i)*DW +: DW];
        for (int k = 1; k < K; k += 2) so += win[(k*NPOS + i)*DW +: DW];
        ce[i] <= se; co[i] <= so;
      end
      for (int p = 0; p < PPC; p++) c1[p] <= win[(4*NPOS + p + 4)*DW +: DW];
      y1 <= wy;
    end
  end

  for (genvar p = 0; p < PPC; p++) begin : g_px
    // stage 2: class sums (two levels)
    logic [DW+5:0] sa2, sb2, sc2_, sd2, se2, sf2;
    logic [DW-1:0] c2a;
    logic [1:0]    ch2a;
    always_ff @(posedge clk) begin
      if (en) begin
        sa2 <= ce[p] + ce[p+2] + ce[p+4];          // C1
        sb2 <= ce[p+6] + ce[p+8];
        sc2_ <= co[p+1] + co[p+3] + co[p+5] + co[p+7];   // C2
        sd2 <= co[p] + co[p+2] + co[p+4];          // G
        se2 <= co[p+6] + co[p+8] + ce[p+1];
        sf2 <= ce[p+3] + ce[p+5] + ce[p+7];
        c2a  <= c1[p];
        ch2a <= cfa[2*(2*y1 + p) +: 2];
      end
    end
    logic [DW+5:0] sc1, sc2, sg;
    logic [DW-1:0] c2;
    logic [1:0]    ch2;
    always_ff @(posedge clk) begin
      if (en) begin
        sc1 <= sa2 + sb2; sc2 <= sc2_; sg <= sd2 + se2 + sf2;
        c2 <= c2a; ch2 <= ch2a;
      end
    end

    // stage 3: averages, rshr(s * round(65536/n), 16)
    logic [DW-1:0] ag3, a13, a23, c3;
    logic [1:0]    ch3;
    always_ff @(posedge clk) begin
      if (en) begin
        logic [DW+18:0] tg, t1, t2;
        tg = sg  * 1638 + 32768;
        t1 = sc1 * 2621 + 32768;
        t2 = sc2 * 4096 + 32768;
        ag3 <= tg[DW+15:16]; a13 <= t1[DW+15:16]; a23 <= t2[DW+15:16];
        c3 <= c2; ch3 <= ch2;
      end
    end

    // stage 4: decision, max, meter products
    logic          act4;
    logic [DW-1:0] m4, c4, a14;
    logic [8:0]    damp4;
    logic [DW+7:0] pa4, pg4, pb4;
    always_ff @(posedge clk) begin
      if (en) begin
        logic [DW:0] tg, t2;
        logic        is_r;
        tg   = ag3 + thres;
        t2   = a23 + thres;
        is_r = (ch3 == 2'd0);
        act4 <= (ch3 == 2'd0 || ch3 == 2'd3) &&
                (c3 > tg) && (c3 > t2) && (a13 > tg) && (a13 > t2);
        m4    <= (ag3 > a23) ? ag3 : a23;
        damp4 <= damp_q8(is_r ? gain_r : gain_b);
        pa4   <= 77 * (is_r ? a13 : a23);           // luma 0.299 R + 0.587 G + 0.114 B
        pg4   <= 150 * ag3;
        pb4   <= 29 * (is_r ? a23 : a13);
        c4 <= c3; a14 <= a13;
      end
    end

    // stage 5: meter, correction product (+ rounding constant)
    logic          act5;
    logic signed [DW+11:0] x5;
    logic [DW-1:0] c5, m5, mt5;
    logic [8:0]    f25;
    always_ff @(posedge clk) begin
      if (en) begin
        logic [DW+9:0] mt;
        x5   <= $signed({1'b0, damp4}) * ($signed({2'b0, c4}) - $signed({2'b0, m4})) + 128;
        mt    = pa4 + pg4 + pb4 + 128;
        mt5  <= mt[DW+7:8];
        f25  <= fade2(a14);
        act5 <= act4; c5 <= c4; m5 <= m4;
      end
    end

    // stage 6: corrected value m + rshr(x, 8) = (m*256 + x + 128) >> 8, fade
    logic          act6;
    logic signed [DW+1:0] cor6;
    logic [DW-1:0] c6;
    logic [8:0]    fade6;
    always_ff @(posedge clk) begin
      if (en) begin
        logic [17:0] f;
        logic signed [DW+11:0] t;
        t     = $signed({2'b0, m5, 8'd0}) + x5;
        cor6 <= (DW+2)'(t >>> 8);
        f     = fade1(mt5) * f25 + 128;
        fade6 <= f[16:8];
        act6 <= act5; c6 <= c5;
      end
    end

    // stage 7: difference, stage 7b: blend product (+ rounding), stage 8: c + rshr(x, 8)
    logic signed [DW+2:0]  d7;
    logic [8:0]            fade7;
    logic                  act7d;
    logic [DW-1:0]         c7d;
    always_ff @(posedge clk) begin
      if (en) begin
        d7 <= cor6 - $signed({2'b0, c6});
        fade7 <= fade6; act7d <= act6; c7d <= c6;
      end
    end
    logic signed [DW+12:0] x7;
    logic                  act7a;
    logic [DW-1:0]         c7a;
    always_ff @(posedge clk) begin
      if (en) begin
        x7 <= $signed({1'b0, fade7}) * d7 + 128;
        act7a <= act7d; c7a <= c7d;
      end
    end
    logic signed [DW+2:0] o7;
    logic                 act7;
    logic [DW-1:0]        c7;
    always_ff @(posedge clk) begin
      if (en) begin
        logic signed [DW+12:0] t;
        t    = $signed({3'b0, c7a, 8'd0}) + x7;
        o7  <= (DW+3)'(t >>> 8);
        act7 <= act7a; c7 <= c7a;
      end
    end

    // stage 9: clip / select
    always_ff @(posedge clk) begin
      if (en)
        od[p*DW +: DW] <= !act7 ? c7 : (o7 < 0) ? '0 :
                          (o7 > (2**DW - 1)) ? {DW{1'b1}} : o7[DW-1:0];
    end
  end
endmodule
