// SPDX-License-Identifier: MIT
//
// isp_mat3 - 3x3 matrix + offset, Q10 (hwmodel.blocks.ccm / csc):
//   out_k = clip(rshr(sum_c m[k][c] * in_c + off_k, 10), 0, 2^OB - 1)
// Pixel = {c2, c1, c0} (c0 at the LSB).  Used as CCM (IB = OB = 10) and
// CSC (RGB8 -> YUV8, IB = OB = 8).
module isp_mat3 #(
  parameter int IB = 10, OB = 10, PPC = 2, CB = 16, OFB = 22
) (
  input  logic                 clk, rst_n,
  input  logic [9*CB-1:0]      coef,         // m[k][c] at [(3k + c)*CB +: CB], signed
  input  logic [3*OFB-1:0]     offs,         // off[k] at [k*OFB +: OFB], signed
  input  logic [PPC*3*IB-1:0]  s_tdata,
  input  logic                 s_tvalid, s_tuser, s_tlast,
  output logic                 s_tready,
  output logic [PPC*3*OB-1:0]  m_tdata,
  output logic                 m_tvalid, m_tuser, m_tlast,
  input  logic                 m_tready
);
  logic [PPC*3*OB-1:0] od;
  logic rdy;
  localparam int LAT = 3, PW = IB + CB + 1, SW = PW + 3;
  logic en;
  assign s_tready = rdy;
  assign en = 1'b1;                       // free-running pipeline
  pipe_out #(.L(LAT), .W(PPC*3*OB), .SW(2)) u_pipe (
    .clk, .rst_n, .in_valid(s_tvalid), .in_side({s_tuser, s_tlast}), .in_ready(rdy),
    .od, .m_tdata, .m_tvalid, .m_side({m_tuser, m_tlast}), .m_tready);

  for (genvar p = 0; p < PPC; p++) begin : g_px
    logic signed [PW-1:0] prod [9];
    logic signed [SW-1:0] acc  [3];
    always_ff @(posedge clk) begin
      if (en) begin
        for (int k = 0; k < 3; k++)
          for (int c = 0; c < 3; c++)
            prod[3*k + c] <= $signed({1'b0, s_tdata[(p*3 + c)*IB +: IB]}) * $signed(coef[(3*k + c)*CB +: CB]);
        for (int k = 0; k < 3; k++)
          acc[k] <= prod[3*k] + prod[3*k + 1] + prod[3*k + 2] + $signed(offs[k*OFB +: OFB]);
        for (int k = 0; k < 3; k++) begin
          logic signed [SW-1:0] r;
          r = (acc[k] + 512) >>> 10;
          od[(p*3 + k)*OB +: OB] <= (r < 0) ? '0 : (r > (2**OB - 1)) ? {OB{1'b1}} : r[OB-1:0];
        end
      end
    end
  end
endmodule
