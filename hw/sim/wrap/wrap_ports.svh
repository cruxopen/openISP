// SPDX-License-Identifier: MIT
// Common port list of every dut_wrap (see hw/sim/tb_stream.sv).
module dut_wrap #(parameter int IW = 20, OW = 20, CW = 32, SB = 13) (
  input  logic          clk, rst_n,
  input  logic [CW-1:0] cfg,
  input  logic [SB-1:0] cfg_width, cfg_height,
  input  logic          wr_en,
  input  logic [15:0]   wr_addr,
  input  logic [31:0]   wr_data,
  input  logic [15:0]   rd_addr,
  output logic [31:0]   rd_data,
  input  logic [IW-1:0] s_tdata,
  input  logic          s_tvalid, s_tuser, s_tlast,
  output logic          s_tready,
  output logic [OW-1:0] m_tdata,
  output logic          m_tvalid, m_tuser, m_tlast,
  input  logic          m_tready
);
