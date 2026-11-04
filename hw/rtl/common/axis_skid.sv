// SPDX-License-Identifier: MIT
//
// axis_skid - AXI4-Stream register slice (2-entry skid buffer): registered
// TVALID / TREADY / data, full throughput.  Breaks the ready path between blocks.
module axis_skid #(
  parameter int W = 32                     // TDATA + sideband bits
) (
  input  logic         clk, rst_n,
  input  logic [W-1:0] s_data,
  input  logic         s_valid,
  output logic         s_ready,
  output logic [W-1:0] m_data,
  output logic         m_valid,
  input  logic         m_ready
);
  logic [W-1:0] sk;
  logic         skv;
  always_ff @(posedge clk) begin
    if (!rst_n) begin
      m_valid <= 1'b0; skv <= 1'b0; s_ready <= 1'b1;
    end else begin
      if (m_ready || !m_valid) begin
        // output register free: take skid entry first, else input
        m_valid <= skv || s_valid && s_ready;
        if (skv) begin m_data <= sk; skv <= 1'b0; end
        else if (s_valid && s_ready) m_data <= s_data;
        s_ready <= 1'b1;
      end else if (s_valid && s_ready) begin
        sk <= s_data; skv <= 1'b1; s_ready <= 1'b0;
      end
    end
  end
endmodule
