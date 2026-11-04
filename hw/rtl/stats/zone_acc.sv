// SPDX-License-Identifier: MIT
//
// zone_acc - NS parallel 32-bit accumulators for the 16 zones of the current
// zone row.  `flush` (with the last beat of a zone row) snapshots the row
// (including that beat) and clears it; the snapshot then drains into the
// statistics RAMs, one zone per cycle (16 cycles; a zone row is always
// longer than that).  Each drained word carries the frame id of its frame.
module zone_acc #(
  parameter int NS = 1,
  parameter int VW = 16             // width of one increment
) (
  input  logic              clk, rst_n,
  input  logic              add,           // accumulate vals into zone zx
  input  logic [3:0]        zx,
  input  logic [NS*VW-1:0]  vals,
  input  logic              flush,
  input  logic [3:0]        zy,            // zone row being flushed
  input  logic [1:0]        fid,           // frame id of the flushed row
  output logic              wr_en,
  output logic [8:0]        wr_addr,       // {fid[0], zy, zx}
  output logic [NS*32-1:0]  wr_data,
  output logic              busy
);
  // input register (timing); everything below works on the *_q signals
  logic              add_q, flush_q;
  logic [3:0]        zx_q, zy_q;
  logic [NS*VW-1:0]  vals_q;
  logic [1:0]        fid_q;
  always_ff @(posedge clk) begin
    if (!rst_n) begin add_q <= 1'b0; flush_q <= 1'b0; end
    else begin add_q <= add; flush_q <= flush; end
    zx_q <= zx; zy_q <= zy; vals_q <= vals; fid_q <= fid;
  end

  logic [31:0] acc  [16][NS];
  logic [31:0] shd  [16][NS];
  logic [4:0]  dcnt;
  logic [3:0]  dzy;
  logic        dbank;

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      for (int z = 0; z < 16; z++) for (int s = 0; s < NS; s++) acc[z][s] <= '0;
    end else if (flush_q) begin
      for (int z = 0; z < 16; z++)
        for (int s = 0; s < NS; s++) begin
          shd[z][s] <= acc[z][s] + ((add_q && zx_q == z) ? 32'(vals_q[s*VW +: VW]) : 32'd0);
          acc[z][s] <= '0;
        end
    end else if (add_q) begin
      for (int s = 0; s < NS; s++) acc[zx_q][s] <= acc[zx_q][s] + 32'(vals_q[s*VW +: VW]);
    end
  end

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      dcnt <= 5'd16; wr_en <= 1'b0;
    end else begin
      wr_en <= 1'b0;
      if (flush_q) begin
        dcnt <= '0; dzy <= zy_q; dbank <= fid_q[0];
      end else if (!dcnt[4]) begin
        wr_en   <= 1'b1;
        wr_addr <= {dbank, dzy, dcnt[3:0]};
        for (int s = 0; s < NS; s++) wr_data[s*32 +: 32] <= shd[dcnt[3:0]][s];
        dcnt <= dcnt + 1'b1;
      end
    end
  end
  assign busy = !dcnt[4] || wr_en || flush_q;
endmodule
