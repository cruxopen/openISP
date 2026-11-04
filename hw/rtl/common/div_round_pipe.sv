// SPDX-License-Identifier: MIT
//
// div_round_pipe - q = (num + den/2) / den, restoring division, one quotient
// bit per stage (latency QW), stall enable `en`.
// Requires den > 0 and a result < 2**QW (the caller guarantees it, e.g. a
// weighted mean of QW-bit samples).  den == 0 returns `alt`.
module div_round_pipe #(
  parameter int NW = 24,           // numerator bits
  parameter int DW = 16,           // denominator bits
  parameter int QW = 8             // quotient bits
) (
  input  logic          clk,
  input  logic          en,
  input  logic [NW-1:0] num,
  input  logic [DW-1:0] den,
  input  logic [QW-1:0] alt,
  output logic [QW-1:0] q
);
  localparam int RW = NW + 1;
  logic [RW-1:0]      r   [QW+1];
  logic [DW-1:0]      d   [QW+1];
  logic [QW-1:0]      qq  [QW+1];
  logic               z   [QW+1];
  logic [QW-1:0]      a   [QW+1];

  always_comb begin
    r[0]  = RW'(num) + RW'(den >> 1);
    d[0]  = den;
    qq[0] = '0;
    z[0]  = (den == 0);
    a[0]  = alt;
  end

  for (genvar s = 0; s < QW; s++) begin : g_st
    localparam int B = QW - 1 - s;
    wire [RW+QW-1:0] sub = {{QW{1'b0}}, d[s]} << B;
    wire             ge  = ({{QW{1'b0}}, r[s]} >= sub);
    always_ff @(posedge clk) begin
      if (en) begin
        r[s+1]  <= ge ? RW'({{QW{1'b0}}, r[s]} - sub) : r[s];
        qq[s+1] <= qq[s] | (QW'(ge) << B);
        d[s+1]  <= d[s];
        z[s+1]  <= z[s];
        a[s+1]  <= a[s];
      end
    end
  end

  assign q = z[QW] ? a[QW] : qq[QW];
endmodule
