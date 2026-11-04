// SPDX-License-Identifier: MIT
//
// row_parity - parity of the current row for point-wise blocks
// (TUSER = start of frame, TLAST = end of line).
module row_parity (
  input  logic clk,
  input  logic rst_n,
  input  logic fire,          // beat accepted
  input  logic tuser,
  input  logic tlast,
  output logic yodd           // parity of the row of the current beat
);
  logic q;
  assign yodd = tuser ? 1'b0 : q;
  always_ff @(posedge clk) begin
    if (!rst_n)              q <= 1'b0;
    else if (fire && tlast)  q <= ~yodd;
    else if (fire && tuser)  q <= 1'b0;
  end
endmodule
