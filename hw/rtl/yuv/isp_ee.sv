// SPDX-License-Identifier: MIT
//
// isp_ee - edge enhancement on Y (hwmodel.blocks.ee), 3x5 high-pass:
//   em = rshr(sum f[i][j] * Y, 3)
//   g  = |em| < t0 ? g0 : |em| <= t1 ? 0 : g1          (Q8)
//   Y' = clip(Y + clip(rshr(g * em, 8), clip_lo, clip_hi), 0, 255)
// Output beat: [PPC*8-1:0] Y', then PPC x 12-bit edge map (saturated to
// +-2047; FCS needs |em| only up to its e1 <= 2047).
module isp_ee #(
  parameter int PPC = 2, MAXW = 3840, SB = 13
) (
  input  logic               clk, rst_n,
  input  logic [SB-1:0]      cfg_width, cfg_height,
  input  logic [15*8-1:0]    filt,           // f[i][j] at [(5i + j)*8 +: 8], signed
  input  logic [2*10-1:0]    gain,           // g0, g1 (Q8, unsigned)
  input  logic [2*16-1:0]    thr,            // t0, t1
  input  logic [2*10-1:0]    emclip,         // lo, hi (signed)
  input  logic [PPC*8-1:0]   s_tdata,
  input  logic               s_tvalid, s_tuser, s_tlast,
  output logic               s_tready,
  output logic [PPC*20-1:0]  m_tdata,
  output logic               m_tvalid, m_tuser, m_tlast,
  input  logic               m_tready
);
  logic [PPC*20-1:0] od;
  logic rdy;
  localparam int NPOS = PPC + 4, LAT = 7;
  logic [3*NPOS*8-1:0] win;
  logic wv, wu, wl, wy, en;

  window_gen #(.DW(8), .PPC(PPC), .KH(3), .KW(5), .MAXW(MAXW), .SB(SB)) u_win (
    .clk, .rst_n, .cfg_width, .cfg_height,
    .s_tdata, .s_tvalid, .s_tuser, .s_tlast, .s_tready,
    .m_tdata(win), .m_tvalid(wv), .m_tuser(wu), .m_tlast(wl), .m_yodd(wy), .m_tready(rdy));

  assign en = 1'b1;                       // free-running pipeline
  pipe_out #(.L(LAT), .W(PPC*20), .SW(2)) u_pipe (
    .clk, .rst_n, .in_valid(wv), .in_side({wu, wl}), .in_ready(rdy),
    .od, .m_tdata, .m_tvalid, .m_side({m_tuser, m_tlast}), .m_tready);

  wire signed [9:0] clo = emclip[9:0], chi = emclip[19:10];

  for (genvar p = 0; p < PPC; p++) begin : g_px
    // s1: products
    logic signed [16:0] pr1 [15];
    logic [7:0]         c1;
    always_ff @(posedge clk) begin
      if (en) begin
        for (int t = 0; t < 15; t++)
          pr1[t] <= $signed(filt[t*8 +: 8]) * $signed({1'b0, win[((t / 5)*NPOS + p + t % 5)*8 +: 8]});
        c1 <= win[(1*NPOS + p + 2)*8 +: 8];
      end
    end
    // s2: row sums
    logic signed [20:0] rs2 [3];
    logic [7:0]         c2;
    always_ff @(posedge clk) begin
      if (en) begin
        for (int i = 0; i < 3; i++)
          rs2[i] <= pr1[5*i] + pr1[5*i+1] + pr1[5*i+2] + pr1[5*i+3] + pr1[5*i+4];
        c2 <= c1;
      end
    end
    // s3: em
    logic signed [20:0] em3;
    logic [7:0]         c3;
    always_ff @(posedge clk) begin
      if (en) begin
        em3 <= (rs2[0] + rs2[1] + rs2[2] + 4) >>> 3;
        c3 <= c2;
      end
    end
    // s4: gain select
    logic [9:0]         g4;
    logic signed [20:0] em4;
    logic signed [11:0] es4;
    logic [7:0]         c4;
    always_ff @(posedge clk) begin
      if (en) begin
        logic [20:0] a;
        a = (em3 < 0) ? 21'(-em3) : 21'(em3);
        g4  <= (a < thr[15:0]) ? gain[9:0] : (a <= thr[31:16]) ? 10'd0 : gain[19:10];
        em4 <= em3;
        es4 <= (em3 > 2047) ? 12'sd2047 : (em3 < -2048) ? -12'sd2048 : 12'(em3);
        c4 <= c3;
      end
    end
    // s4b: gain * em
    logic signed [31:0] gm4;
    logic signed [11:0] es4b;
    logic [7:0]         c4b;
    always_ff @(posedge clk) begin
      if (en) begin
        gm4 <= $signed({1'b0, g4}) * em4;
        es4b <= es4; c4b <= c4;
      end
    end
    // s5: delta
    logic signed [9:0]  dl5;
    logic signed [11:0] es5;
    logic [7:0]         c5;
    always_ff @(posedge clk) begin
      if (en) begin
        logic signed [31:0] d;
        d = (gm4 + 128) >>> 8;
        dl5 <= (d < clo) ? clo : (d > chi) ? chi : 10'(d);
        es5 <= es4b; c5 <= c4b;
      end
    end
    // s6: output
    always_ff @(posedge clk) begin
      if (en) begin
        logic signed [10:0] o;
        o = $signed({3'b0, c5}) + dl5;
        od[p*8 +: 8] <= (o < 0) ? 8'd0 : (o > 255) ? 8'd255 : o[7:0];
        od[PPC*8 + p*12 +: 12] <= es5;
      end
    end
  end
endmodule
