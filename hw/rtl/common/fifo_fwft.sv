// SPDX-License-Identifier: MIT
//
// fifo_fwft - first-word-fall-through FIFO on a registered-read RAM
// (block RAM / URAM), full throughput, 2-entry output buffer.
module fifo_fwft #(
  parameter int    W     = 32,
  parameter int    DEPTH = 1024,          // power of two
  parameter string STYLE = "block"          // "block" or "ultra"
) (
  input  logic         clk, rst_n,
  input  logic [W-1:0] s_data,
  input  logic         s_valid,
  output logic         s_ready,
  output logic [W-1:0] m_data,
  output logic         m_valid,
  input  logic         m_ready
);
  localparam int AW = $clog2(DEPTH);
  logic [AW-1:0] wp, rp;
  logic [AW:0]   mcnt;                    // words in RAM not yet read
  logic          pend;                    // RAM read data valid in rd_q
  logic [W-1:0]  rd_q, b [2];
  logic [1:0]    occ;
  logic          bw, br;

  wire push  = s_valid && s_ready;
  wire pop   = m_valid && m_ready;
  wire issue = (mcnt != 0) && ((occ + pend - pop) < 2);

  assign s_ready = (mcnt + pend + occ) < DEPTH;
  assign m_valid = (occ != 0);
  assign m_data  = b[br];

  if (STYLE == "ultra") begin : g_u
    (* ram_style = "ultra" *) logic [W-1:0] mem [DEPTH];
    always_ff @(posedge clk) begin
      if (push) mem[wp] <= s_data;
      if (issue) rd_q <= mem[rp];
    end
  end else begin : g_b
    (* ram_style = "block" *) logic [W-1:0] mem [DEPTH];
    always_ff @(posedge clk) begin
      if (push) mem[wp] <= s_data;
      if (issue) rd_q <= mem[rp];
    end
  end
  always_ff @(posedge clk) if (pend) b[bw] <= rd_q;

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      wp <= '0; rp <= '0; mcnt <= '0; pend <= 1'b0; occ <= '0; bw <= 1'b0; br <= 1'b0;
    end else begin
      if (push)  wp <= wp + 1'b1;
      if (issue) rp <= rp + 1'b1;
      mcnt <= mcnt + push - issue;
      pend <= issue;
      if (pend) bw <= ~bw;
      if (pop)  br <= ~br;
      occ  <= occ + pend - pop;
    end
  end
endmodule
