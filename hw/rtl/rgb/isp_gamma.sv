// SPDX-License-Identifier: MIT
//
// isp_gamma - 10 -> 8 bit gamma LUT (hwmodel.blocks.gamma), one table for
// R, G and B, PPC*3 lookups per beat (replicated block RAM).
// Table write: lut_we / lut_addr / lut_data (load while idle).
module isp_gamma #(
  parameter int PPC = 2
) (
  input  logic               clk, rst_n,
  input  logic               lut_we,
  input  logic [9:0]         lut_addr,
  input  logic [7:0]         lut_data,
  input  logic [PPC*30-1:0]  s_tdata,
  input  logic               s_tvalid, s_tuser, s_tlast,
  output logic               s_tready,
  output logic [PPC*24-1:0]  m_tdata,
  output logic               m_tvalid, m_tuser, m_tlast,
  input  logic               m_tready
);
  logic [PPC*24-1:0] od;
  logic rdy;
  logic en;
  assign s_tready = rdy;
  assign en = 1'b1;                       // free-running pipeline
  pipe_out #(.L(1), .W(PPC*24), .SW(2)) u_pipe (
    .clk, .rst_n, .in_valid(s_tvalid), .in_side({s_tuser, s_tlast}), .in_ready(rdy),
    .od, .m_tdata, .m_tvalid, .m_side({m_tuser, m_tlast}), .m_tready);

  lut_mem #(.AW(10), .DW(8), .NRD(3*PPC), .STYLE("block")) u_lut (
    .clk, .en, .we(lut_we), .waddr(lut_addr), .wdata(lut_data),
    .raddr(s_tdata), .rdata(od));
endmodule
