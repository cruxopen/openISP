// SPDX-License-Identifier: MIT
//
// axil_regbus - AXI4-Lite slave -> simple word-addressed register bus.
//   write: wr_en pulse with wr_addr / wr_data (32-bit writes, WSTRB ignored)
//   read : rd_addr held, rd_data sampled RL cycles later
// One write and one read in flight at a time; AW and W may come in any order.
// Responses are always OKAY.  Byte address bits [1:0] are ignored.
module axil_regbus #(
  parameter int AW = 18,                // byte address width
  parameter int RL = 2                  // read latency of the register bus
) (
  input  logic          clk, rst_n,
  input  logic [AW-1:0] s_axi_awaddr,
  input  logic          s_axi_awvalid,
  output logic          s_axi_awready,
  input  logic [31:0]   s_axi_wdata,
  input  logic [3:0]    s_axi_wstrb,
  input  logic          s_axi_wvalid,
  output logic          s_axi_wready,
  output logic [1:0]    s_axi_bresp,
  output logic          s_axi_bvalid,
  input  logic          s_axi_bready,
  input  logic [AW-1:0] s_axi_araddr,
  input  logic          s_axi_arvalid,
  output logic          s_axi_arready,
  output logic [31:0]   s_axi_rdata,
  output logic [1:0]    s_axi_rresp,
  output logic          s_axi_rvalid,
  input  logic          s_axi_rready,
  output logic          wr_en,
  output logic [AW-3:0] wr_addr,
  output logic [31:0]   wr_data,
  output logic [AW-3:0] rd_addr,
  input  logic [31:0]   rd_data
);
  // ---------------------------------------------------------------- write
  logic aw_ok, w_ok;
  assign s_axi_awready = !aw_ok && !s_axi_bvalid;
  assign s_axi_wready  = !w_ok && !s_axi_bvalid;
  assign s_axi_bresp   = 2'b00;

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      aw_ok <= 1'b0; w_ok <= 1'b0; s_axi_bvalid <= 1'b0; wr_en <= 1'b0;
      wr_addr <= '0; wr_data <= '0;
    end else begin
      wr_en <= 1'b0;
      if (s_axi_awvalid && s_axi_awready) begin aw_ok <= 1'b1; wr_addr <= s_axi_awaddr[AW-1:2]; end
      if (s_axi_wvalid && s_axi_wready)   begin w_ok <= 1'b1;  wr_data <= s_axi_wdata; end
      if (aw_ok && w_ok) begin
        wr_en <= 1'b1; aw_ok <= 1'b0; w_ok <= 1'b0; s_axi_bvalid <= 1'b1;
      end
      if (s_axi_bvalid && s_axi_bready) s_axi_bvalid <= 1'b0;
    end
  end

  // ---------------------------------------------------------------- read
  logic [RL:0] rpipe;                   // one-hot position of the read in flight
  wire rbusy = |rpipe || s_axi_rvalid;
  assign s_axi_arready = !rbusy;
  assign s_axi_rresp   = 2'b00;

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      rpipe <= '0; s_axi_rvalid <= 1'b0; rd_addr <= '0; s_axi_rdata <= '0;
    end else begin
      rpipe <= {rpipe[RL-1:0], s_axi_arvalid && s_axi_arready};
      if (s_axi_arvalid && s_axi_arready) rd_addr <= s_axi_araddr[AW-1:2];
      if (rpipe[RL]) begin s_axi_rvalid <= 1'b1; s_axi_rdata <= rd_data; end
      if (s_axi_rvalid && s_axi_rready) s_axi_rvalid <= 1'b0;
    end
  end
endmodule
