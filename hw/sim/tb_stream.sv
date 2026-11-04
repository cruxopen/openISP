// SPDX-License-Identifier: MIT
//
// Generic file-driven testbench for every ISP block (Verilator >= 5 --timing,
// Vivado xsim).  The block is wrapped in a `dut_wrap` module
// (hw/sim/wrap/*.sv) with this fixed port list:
//   clk, rst_n, cfg[CW], cfg_width, cfg_height,
//   wr_en, wr_addr[16], wr_data[32]          table / register writes
//   rd_addr[16], rd_data[32]                 statistics read (latency <= 2)
//   s_tdata[IW] s_tvalid s_tuser s_tlast s_tready,
//   m_tdata[OW] m_tvalid m_tuser m_tlast m_tready
//
// stimulus (+STIM): one record per line
//   C <hex>            static configuration vector (CW bits)
//   W <addr> <data>    one write cycle (hex)
//   F <w> <h> <n>      frame size, followed by n data beats (hex)
//   J <hex>            one beat with TUSER = TLAST = 0 (junk before SOF)
//   E <n>              wait until n output beats were received, then idle
//   R <addr> <n>       read n consecutive words (hex addr)
//   Q <n>              wait until the output has been idle for n cycles
//   M                  write a marker line "M" to the output
// +IDLE=<n> (optional): after +NOUT beats keep collecting until the output
// has been idle for n cycles (streams with an unknown number of extra beats,
// e.g. a truncated frame); the result is DONE when at least NOUT arrived.
// output (+OUT): "O <tuser> <tlast> <hex>" per output beat, "R <addr> <hex>"
// per read word.  +SEED, +VALID_PCT, +READY_PCT control random flow control.
module tb_stream;
  parameter int IW = 20, OW = 20, CW = 32, SB = 13;

  logic clk = 0, rst_n = 0;
  always #2 clk = ~clk;

  logic [CW-1:0] cfg = '0;
  logic [SB-1:0] cfg_w = '0, cfg_h = '0;
  logic          wr_en = 0;
  logic [15:0]   wr_addr = '0, rd_addr = '0;
  logic [31:0]   wr_data = '0, rd_data;
  logic [IW-1:0] s_tdata = '0;
  logic          s_tvalid = 0, s_tuser = 0, s_tlast = 0, s_tready;
  logic [OW-1:0] m_tdata;
  logic          m_tvalid, m_tuser, m_tlast, m_tready = 0;

  dut_wrap #(.IW(IW), .OW(OW), .CW(CW), .SB(SB)) dut (
    .clk, .rst_n, .cfg, .cfg_width(cfg_w), .cfg_height(cfg_h),
    .wr_en, .wr_addr, .wr_data, .rd_addr, .rd_data,
    .s_tdata, .s_tvalid, .s_tuser, .s_tlast, .s_tready,
    .m_tdata, .m_tvalid, .m_tuser, .m_tlast, .m_tready);

  string stim_path, out_path;
  int fi, fo, nout = 0, nexp = 0, seed = 1, vpct = 100, rpct = 100, idle = 0;
  longint cycles = 0;

  int unsigned rs = 32'h1234_5678;            // xorshift32
  function automatic int unsigned rnd100();
    rs ^= rs << 13; rs ^= rs >> 17; rs ^= rs << 5;
    return rs % 100;
  endfunction

  always @(posedge clk) begin
    cycles <= cycles + 1;
    if (rst_n) begin
      if (m_tvalid && m_tready) begin
        $fwrite(fo, "O %0d %0d %h\n", m_tuser, m_tlast, m_tdata);
        nout = nout + 1;
      end
      m_tready <= rnd100() < rpct;
    end
  end

  // TVALID is only dropped for an idle cycle, never "<= 0" then "<= 1" in
  // the same time step.
  task automatic send(input logic [IW-1:0] d, input logic u, input logic l);
    while (rnd100() >= vpct) begin
      s_tvalid <= 1'b0;
      @(posedge clk);
    end
    s_tdata <= d; s_tuser <= u; s_tlast <= l; s_tvalid <= 1'b1;
    do @(posedge clk); while (!s_tready);
  endtask

  initial begin
    string tag;
    int w, h, n, r, cnt;
    logic [IW-1:0] d;
    logic [CW-1:0] c;
    logic [31:0] a, v;
    if (!$value$plusargs("STIM=%s", stim_path)) $fatal(1, "+STIM missing");
    if (!$value$plusargs("OUT=%s", out_path))   $fatal(1, "+OUT missing");
    r = $value$plusargs("NOUT=%d", nexp);
    r = $value$plusargs("SEED=%d", seed);
    r = $value$plusargs("VALID_PCT=%d", vpct);
    r = $value$plusargs("READY_PCT=%d", rpct);
    r = $value$plusargs("IDLE=%d", idle);
    rs = 32'h9E37_79B9 ^ seed;
    fi = $fopen(stim_path, "r");
    fo = $fopen(out_path, "w");
    repeat (5) @(posedge clk);
    rst_n <= 1;
    @(posedge clk);
    while ($fscanf(fi, "%s", tag) == 1) begin
      // wr_en stays high across consecutive W records (no 0-then-1 NBA
      // pair in one time step, which Verilator does not order)
      if (tag != "W") wr_en <= 1'b0;
      if (tag == "C") begin
        r = $fscanf(fi, "%h", c);
        cfg <= c;
        @(posedge clk);
      end else if (tag == "W") begin
        r = $fscanf(fi, "%h %h", a, v);
        wr_en <= 1'b1; wr_addr <= a[15:0]; wr_data <= v;
        @(posedge clk);
      end else if (tag == "F") begin
        r = $fscanf(fi, "%d %d %d", w, h, n);
        cfg_w <= SB'(w); cfg_h <= SB'(h);
        @(posedge clk);
        for (int b = 0; b < n; b++) begin
          r = $fscanf(fi, "%h", d);
          send(d, (b == 0), ((b + 1) % (w / 2) == 0));
        end
        s_tvalid <= 1'b0;
      end else if (tag == "J") begin
        r = $fscanf(fi, "%h", d);
        send(d, 1'b0, 1'b0);
        s_tvalid <= 1'b0;
      end else if (tag == "E") begin
        r = $fscanf(fi, "%d", n);
        s_tvalid <= 1'b0;
        while (nout < n && cycles < 64'd20_000_000) @(posedge clk);
        repeat (300) @(posedge clk);
      end else if (tag == "Q") begin
        int last, q;
        r = $fscanf(fi, "%d", n);
        s_tvalid <= 1'b0;
        last = nout; q = 0;
        while (q < n && cycles < 64'd20_000_000) begin
          @(posedge clk);
          if (nout != last) begin last = nout; q = 0; end else q++;
        end
      end else if (tag == "M") begin
        $fwrite(fo, "M\n");                 // marker: start of a read group
      end else if (tag == "R") begin
        r = $fscanf(fi, "%h %d", a, cnt);
        for (int i = 0; i < cnt; i++) begin
          rd_addr <= a[15:0] + 16'(i);
          repeat (3) @(posedge clk);
          $fwrite(fo, "R %0h %h\n", a + i, rd_data);
        end
      end
    end
    s_tvalid <= 1'b0; wr_en <= 1'b0;
    while (nout < nexp && cycles < 64'd20_000_000) @(posedge clk);
    if (idle > 0) begin
      int last = nout, q = 0;
      while (q < idle && cycles < 64'd20_000_000) begin
        @(posedge clk);
        if (nout != last) begin last = nout; q = 0; end else q++;
      end
    end
    repeat (20) @(posedge clk);
    $fclose(fo);
    if (idle > 0 && nout >= nexp) $display("TB_RESULT DONE %0d beats %0d cycles", nout, cycles);
    else if (nout != nexp) $display("TB_RESULT FAIL got %0d of %0d beats", nout, nexp);
    else              $display("TB_RESULT DONE %0d beats %0d cycles", nout, cycles);
    $finish;
  end
endmodule
