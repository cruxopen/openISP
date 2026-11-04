// SPDX-License-Identifier: MIT
//
// pipe_out - output side of a free-running L-stage pipeline (no clock enable).
//
// The block's data registers advance every cycle; a beat accepted at the
// input (in_valid && in_ready) appears in the block's last stage `od`
// exactly L cycles later and is pushed into a small output FIFO.  Credits
// (beats in flight + in the FIFO) never exceed the FIFO depth, so in_ready
// depends on registers only: no high-fanout enable, no combinational ready
// path between blocks.
module pipe_out #(
  parameter int L  = 1,
  parameter int W  = 8,
  parameter int SW = 2
) (
  input  logic          clk, rst_n,
  input  logic          in_valid,
  input  logic [SW-1:0] in_side,
  output logic          in_ready,
  input  logic [W-1:0]  od,          // stage-L data of the block
  output logic [W-1:0]  m_tdata,
  output logic          m_tvalid,
  output logic [SW-1:0] m_side,
  input  logic          m_tready
);
  localparam int D  = 1 << $clog2(L + 4);
  localparam int AW = $clog2(D);
  logic [L-1:0]  v;
  logic [SW-1:0] sd [L];
  logic [AW:0]   cred, cnt;
  logic [AW-1:0] wp, rp;
  (* ram_style = "distributed" *) logic [W+SW-1:0] mem [D];

  wire acc  = in_valid && in_ready;
  wire push = v[L-1];
  wire pop  = m_tvalid && m_tready;
  assign in_ready = (cred < D);
  assign m_tvalid = (cnt != 0);
  assign {m_side, m_tdata} = mem[rp];

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      v <= '0; cred <= '0; cnt <= '0; wp <= '0; rp <= '0;
    end else begin
      v[0] <= acc;
      for (int i = 1; i < L; i++) v[i] <= v[i-1];
      cred <= cred + acc - pop;
      cnt  <= cnt + push - pop;
      if (push) wp <= wp + 1'b1;
      if (pop)  rp <= rp + 1'b1;
    end
  end
  always_ff @(posedge clk) begin
    sd[0] <= in_side;
    for (int i = 1; i < L; i++) sd[i] <= sd[i-1];
    if (push) mem[wp] <= {sd[L-1], od};
  end
endmodule
