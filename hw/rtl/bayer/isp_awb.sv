// SPDX-License-Identifier: MIT
//
// isp_awb - white balance gains (hwmodel.blocks.awb_gain): clip(rshr(raw * g, 10), 1023),
// gains Q10, 14 bit (up to 16x).  cfa as in isp_blc.
module isp_awb #(
  parameter int DW = 10, PPC = 2, GW = 14
) (
  input  logic                clk, rst_n,
  input  logic [7:0]          cfa,
  input  logic [4*GW-1:0]     gain,        // {b, gb, gr, r}
  input  logic [PPC*DW-1:0]   s_tdata,
  input  logic                s_tvalid, s_tuser, s_tlast,
  output logic                s_tready,
  output logic [PPC*DW-1:0]   m_tdata,
  output logic                m_tvalid, m_tuser, m_tlast,
  input  logic                m_tready
);
  logic [PPC*DW-1:0] od;
  logic rdy;
  localparam int LAT = 2;
  logic en, yodd;
  assign s_tready = rdy;

  row_parity u_rp (.clk, .rst_n, .fire(s_tvalid && rdy), .tuser(s_tuser), .tlast(s_tlast), .yodd);
  assign en = 1'b1;                       // free-running pipeline
  pipe_out #(.L(LAT), .W(PPC*DW), .SW(2)) u_pipe (
    .clk, .rst_n, .in_valid(s_tvalid), .in_side({s_tuser, s_tlast}), .in_ready(rdy),
    .od, .m_tdata, .m_tvalid, .m_side({m_tuser, m_tlast}), .m_tready);

  logic [DW+GW-1:0] prod [PPC];
  always_ff @(posedge clk) begin
    if (en)
      for (int p = 0; p < PPC; p++)
        prod[p] <= s_tdata[p*DW +: DW] * gain[cfa[2*(2*yodd + (p & 1)) +: 2]*GW +: GW];
  end
  always_ff @(posedge clk) begin
    if (en)
      for (int p = 0; p < PPC; p++) begin
        logic [DW+GW-1:0] r;
        r = (prod[p] + 512) >> 10;
        od[p*DW +: DW] <= (r > (DW+GW)'(2**DW - 1)) ? {DW{1'b1}} : r[DW-1:0];
      end
  end
endmodule
