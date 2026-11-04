// SPDX-License-Identifier: MIT
//
// Testbench for axil_regbus: random AXI4-Lite master (independent AW / W
// delays, random BREADY / RREADY) against a register memory with read
// latency 2.  Stimulus (+STIM): "W <byte addr> <data>" or "R <byte addr>"
// (hex).  Output (+OUT): "R <byte addr> <data>" per read.  +SEED, +PCT.
module tb_axil;
  logic clk = 0, rst_n = 0;
  always #2 clk = ~clk;

  logic [17:0] awaddr = '0, araddr = '0;
  logic        awvalid = 0, wvalid = 0, bready = 0, arvalid = 0, rready = 0;
  logic [31:0] wdata = '0, rdata;
  logic        awready, wready, bvalid, arready, rvalid;
  logic [1:0]  bresp, rresp;
  logic        wr_en;
  logic [15:0] wr_addr, rd_addr;
  logic [31:0] wr_data, rd_data;

  axil_regbus #(.AW(18), .RL(2)) dut (
    .clk, .rst_n,
    .s_axi_awaddr(awaddr), .s_axi_awvalid(awvalid), .s_axi_awready(awready),
    .s_axi_wdata(wdata), .s_axi_wstrb(4'hF), .s_axi_wvalid(wvalid), .s_axi_wready(wready),
    .s_axi_bresp(bresp), .s_axi_bvalid(bvalid), .s_axi_bready(bready),
    .s_axi_araddr(araddr), .s_axi_arvalid(arvalid), .s_axi_arready(arready),
    .s_axi_rdata(rdata), .s_axi_rresp(rresp), .s_axi_rvalid(rvalid), .s_axi_rready(rready),
    .wr_en, .wr_addr, .wr_data, .rd_addr, .rd_data);

  // register memory, read latency 2 (like isp_top)
  logic [31:0] mem [1024];
  logic [31:0] rd1;
  initial for (int i = 0; i < 1024; i++) mem[i] = 32'hdead_0000 | i;
  always @(posedge clk) begin
    if (wr_en) mem[wr_addr[9:0]] <= wr_data;
    rd1 <= mem[rd_addr[9:0]];
    rd_data <= rd1;
  end

  int unsigned rs;
  int pct = 50, seed = 1, fi, fo, r;
  function automatic int unsigned rnd100();
    rs ^= rs << 13; rs ^= rs >> 17; rs ^= rs << 5;
    return rs % 100;
  endfunction
  task automatic idle_rand();
    while (rnd100() >= pct) @(posedge clk);
  endtask

  always @(posedge clk) begin
    bready <= rnd100() < pct;
    rready <= rnd100() < pct;
  end

  task automatic axi_write(input logic [17:0] a, input logic [31:0] d);
    fork
      begin idle_rand(); awaddr <= a; awvalid <= 1'b1;
            do @(posedge clk); while (!awready); awvalid <= 1'b0; end
      begin idle_rand(); wdata <= d; wvalid <= 1'b1;
            do @(posedge clk); while (!wready); wvalid <= 1'b0; end
    join
    do @(posedge clk); while (!(bvalid && bready));
    if (bresp != 2'b00) $display("TB_RESULT FAIL bresp");
  endtask

  task automatic axi_read(input logic [17:0] a, output logic [31:0] d);
    idle_rand();
    araddr <= a; arvalid <= 1'b1;
    do @(posedge clk); while (!arready);
    arvalid <= 1'b0;
    do @(posedge clk); while (!(rvalid && rready));
    d = rdata;
  endtask

  initial begin
    string stim, outp, tag;
    logic [31:0] a, d;
    if (!$value$plusargs("STIM=%s", stim)) $fatal(1, "+STIM missing");
    if (!$value$plusargs("OUT=%s", outp))  $fatal(1, "+OUT missing");
    r = $value$plusargs("SEED=%d", seed);
    r = $value$plusargs("PCT=%d", pct);
    rs = 32'h9E37_79B9 ^ seed;
    fi = $fopen(stim, "r");
    fo = $fopen(outp, "w");
    repeat (5) @(posedge clk);
    rst_n <= 1;
    @(posedge clk);
    while ($fscanf(fi, "%s", tag) == 1) begin
      if (tag == "W") begin
        r = $fscanf(fi, "%h %h", a, d);
        axi_write(a[17:0], d);
      end else begin
        r = $fscanf(fi, "%h", a);
        axi_read(a[17:0], d);
        $fwrite(fo, "R %0h %h\n", a, d);
      end
    end
    $fclose(fo);
    $display("TB_RESULT DONE");
    $finish;
  end
endmodule
