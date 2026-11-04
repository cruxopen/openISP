// SPDX-License-Identifier: MIT
//
// isp_blc - black level subtraction + Gr/Gb crosstalk (hwmodel.blocks.blc).
//   v = clip(raw - bl[ch], 0, 1023)
//   Gr += rshr(alpha * R, 8), Gb += rshr(beta * B, 8) with R/B of the same
//   2x2 quad (always the other pixel of the same beat for PPC = 2), clip.
// cfa: channel id (0 R, 1 Gr, 2 Gb, 3 B) of pixel (yodd, xodd) at bits
//      [2*(2*yodd + xodd) +: 2].
module isp_blc #(
  parameter int DW = 10, PPC = 2
) (
  input  logic                clk, rst_n,
  input  logic [7:0]          cfa,
  input  logic [4*DW-1:0]     bl,          // {b, gb, gr, r}
  input  logic signed [9:0]   alpha, beta, // Q8
  input  logic [PPC*DW-1:0]   s_tdata,
  input  logic                s_tvalid, s_tuser, s_tlast,
  output logic                s_tready,
  output logic [PPC*DW-1:0]   m_tdata,
  output logic                m_tvalid, m_tuser, m_tlast,
  input  logic                m_tready
);
  logic [PPC*DW-1:0] od;
  logic rdy;
  localparam int LAT = 3;
  localparam logic [DW-1:0] MAXV = '1;
  logic en, yodd;
  assign s_tready = rdy;

  row_parity u_rp (.clk, .rst_n, .fire(s_tvalid && rdy), .tuser(s_tuser), .tlast(s_tlast), .yodd);
  assign en = 1'b1;                       // free-running pipeline
  pipe_out #(.L(LAT), .W(PPC*DW), .SW(2)) u_pipe (
    .clk, .rst_n, .in_valid(s_tvalid), .in_side({s_tuser, s_tlast}), .in_ready(rdy),
    .od, .m_tdata, .m_tvalid, .m_side({m_tuser, m_tlast}), .m_tready);

  logic [DW-1:0] v1 [PPC];
  logic [1:0]    ch1 [PPC];
  always_ff @(posedge clk) begin
    if (en)
      for (int p = 0; p < PPC; p++) begin
        logic [1:0] ch;
        logic signed [DW+1:0] d;
        ch = cfa[2*(2*yodd + (p & 1)) +: 2];
        d  = $signed({2'b0, s_tdata[p*DW +: DW]}) - $signed({2'b0, bl[ch*DW +: DW]});
        v1[p]  <= (d < 0) ? '0 : d[DW-1:0];
        ch1[p] <= ch;
      end
  end

  logic signed [DW+11:0] x2 [PPC];
  logic [DW-1:0]         v2 [PPC];
  always_ff @(posedge clk) begin
    if (en)
      for (int p = 0; p < PPC; p++) begin
        x2[p] <= $signed({1'b0, v1[p ^ 1]}) * ((ch1[p] == 2'd1) ? alpha : (ch1[p] == 2'd2) ? beta : 10'sd0);
        v2[p] <= v1[p];
      end
  end
  always_ff @(posedge clk) begin
    if (en)
      for (int p = 0; p < PPC; p++) begin
        logic signed [DW+3:0] o;
        o = $signed({4'b0, v2[p]}) + $signed((x2[p] + 128) >>> 8);
        od[p*DW +: DW] <= (o < 0) ? '0 : (o > $signed({4'b0, MAXV})) ? MAXV : o[DW-1:0];
      end
  end
endmodule
