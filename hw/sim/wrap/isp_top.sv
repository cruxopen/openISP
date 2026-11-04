// full pipeline; configuration through the register bus (W records), statistics through R.
// Read addresses 0xFFFE and 0xFFFF return the irq output (bit 0).
`include "wrap_ports.svh"
  logic irq;
  logic [31:0] rd_isp;
  logic [15:0] ra1, ra2;
  always_ff @(posedge clk) begin ra1 <= rd_addr; ra2 <= ra1; end
  assign rd_data = (ra2[15:1] == 15'h7FFF) ? {31'd0, irq} : rd_isp;
  isp_top #(.SB(SB)) u (.clk, .rst_n, .wr_en, .wr_addr, .wr_data, .rd_addr, .rd_data(rd_isp),
    .s_tdata, .s_tvalid, .s_tuser, .s_tlast, .s_tready,
    .m_tdata, .m_tvalid, .m_tuser, .m_tlast, .m_tready, .irq);
endmodule
