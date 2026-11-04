// SPDX-License-Identifier: MIT
//
// lut_mem - writable lookup table with NRD independent read ports.
// One write port (configuration), NRD replicated copies, registered read
// (latency 1, gated by `en`).  STYLE = "block" or "distributed".
// Write the table while the pipeline is idle (between streams / vblank).
module lut_mem #(
  parameter int    AW    = 10,
  parameter int    DW    = 8,
  parameter int    NRD   = 1,
  parameter string STYLE = "block"
) (
  input  logic                clk,
  input  logic                en,
  input  logic                we,
  input  logic [AW-1:0]       waddr,
  input  logic [DW-1:0]       wdata,
  input  logic [NRD*AW-1:0]   raddr,
  output logic [NRD*DW-1:0]   rdata
);
  for (genvar g = 0; g < NRD; g++) begin : g_copy
    if (STYLE == "block") begin : g_b
      (* ram_style = "block" *) logic [DW-1:0] mem [1 << AW];
      always_ff @(posedge clk) begin
        if (we) mem[waddr] <= wdata;
        if (en) rdata[g*DW +: DW] <= mem[raddr[g*AW +: AW]];
      end
    end else begin : g_d
      (* ram_style = "distributed" *) logic [DW-1:0] mem [1 << AW];
      always_ff @(posedge clk) begin
        if (we) mem[waddr] <= wdata;
        if (en) rdata[g*DW +: DW] <= mem[raddr[g*AW +: AW]];
      end
    end
  end
endmodule
