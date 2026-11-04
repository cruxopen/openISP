// SPDX-License-Identifier: MIT
//
// isp_cfa - Malvar-He-Cutler demosaic (hwmodel.blocks.cfa_malvar), 5x5 window.
// Output per pixel: {B, G, R}, DW bits each (pixel p at [p*3*DW +: 3*DW]).
module isp_cfa #(
  parameter int DW = 10, PPC = 2, MAXW = 3840, SB = 13
) (
  input  logic                clk, rst_n,
  input  logic [SB-1:0]       cfg_width, cfg_height,
  input  logic [7:0]          cfa,
  input  logic [PPC*DW-1:0]   s_tdata,
  input  logic                s_tvalid, s_tuser, s_tlast,
  output logic                s_tready,
  output logic [PPC*3*DW-1:0] m_tdata,
  output logic                m_tvalid, m_tuser, m_tlast,
  input  logic                m_tready
);
  logic [PPC*3*DW-1:0] od;
  logic rdy;
  localparam int K = 5, NPOS = PPC + 4, LAT = 3, SW = DW + 7;
  logic [K*NPOS*DW-1:0] win;
  logic wv, wu, wl, wy, en;

  window_gen #(.DW(DW), .PPC(PPC), .KH(K), .KW(K), .MAXW(MAXW), .SB(SB)) u_win (
    .clk, .rst_n, .cfg_width, .cfg_height,
    .s_tdata, .s_tvalid, .s_tuser, .s_tlast, .s_tready,
    .m_tdata(win), .m_tvalid(wv), .m_tuser(wu), .m_tlast(wl), .m_yodd(wy), .m_tready(rdy));

  assign en = 1'b1;                       // free-running pipeline
  pipe_out #(.L(LAT), .W(PPC*3*DW), .SW(2)) u_pipe (
    .clk, .rst_n, .in_valid(wv), .in_side({wu, wl}), .in_ready(rdy),
    .od, .m_tdata, .m_tvalid, .m_side({m_tuser, m_tlast}), .m_tready);

  function automatic logic [DW-1:0] clip_rshr4(logic signed [SW-1:0] x);
    logic signed [SW-1:0] r;
    r = (x + 8) >>> 4;
    return (r < 0) ? '0 : (r > (2**DW - 1)) ? {DW{1'b1}} : r[DW-1:0];
  endfunction

  for (genvar p = 0; p < PPC; p++) begin : g_px
    function automatic logic signed [SW-1:0] px(int dy, int dx);
      return $signed({{(SW-DW){1'b0}}, win[((2+dy)*NPOS + p + 2 + dx)*DW +: DW]});
    endfunction

    // stage 1: partial sums
    logic signed [SW-1:0] c1, h1, v1, h2, v2, d1;
    logic [1:0]           ch1;
    always_ff @(posedge clk) begin
      if (en) begin
        c1  <= px(0, 0);
        h1  <= px(0, -1) + px(0, 1);
        v1  <= px(-1, 0) + px(1, 0);
        h2  <= px(0, -2) + px(0, 2);
        v2  <= px(-2, 0) + px(2, 0);
        d1  <= px(-1, -1) + px(-1, 1) + px(1, -1) + px(1, 1);
        ch1 <= cfa[2*(2*wy + p) +: 2];
      end
    end

    // stage 2: kernels (x16)
    logic signed [SW-1:0] k_g, k_o, k_h, k_v, c2;
    logic [1:0]           ch2;
    always_ff @(posedge clk) begin
      if (en) begin
        k_g <= 8 * c1 - 2 * (h2 + v2) + 4 * (h1 + v1);
        k_o <= 12 * c1 - 3 * (h2 + v2) + 4 * d1;
        k_h <= 10 * c1 - 2 * h2 - 2 * d1 + v2 + 8 * h1;
        k_v <= 10 * c1 - 2 * v2 - 2 * d1 + h2 + 8 * v1;
        c2  <= c1;
        ch2 <= ch1;
      end
    end

    // stage 3: round, clip, select
    always_ff @(posedge clk) begin
      if (en) begin
        logic [DW-1:0] g, o, h, v, c;
        g = clip_rshr4(k_g); o = clip_rshr4(k_o);
        h = clip_rshr4(k_h); v = clip_rshr4(k_v);
        c = c2[DW-1:0];
        unique case (ch2)
          2'd0: od[p*3*DW +: 3*DW] <= {o, g, c};    // R site
          2'd1: od[p*3*DW +: 3*DW] <= {v, c, h};    // Gr
          2'd2: od[p*3*DW +: 3*DW] <= {h, c, v};    // Gb
          default: od[p*3*DW +: 3*DW] <= {c, g, o}; // B
        endcase
      end
    end
  end
endmodule
