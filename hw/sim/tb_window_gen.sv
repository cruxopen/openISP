// SPDX-License-Identifier: MIT
//
// File-driven testbench for window_gen (Verilator >= 5 --timing, Vivado xsim).
//
// stimulus file (+STIM=path), one record per line:
//   F <width> <height>   start of a frame; the following width*height/PPC
//                        lines are its data beats (hex, PPC*DW bits)
//   J <hex>              junk beat sent before a SOF (must be dropped)
// output file (+OUT=path): one line per output beat
//   <tuser> <tlast> <yodd> <hex window>
// +NOUT=<n> expected output beats, +SEED=<n>, +VALID_PCT, +READY_PCT (0..100)
module tb_window_gen;
  parameter int DW = 10, PPC = 2, KH = 5, KW = 5, MAXW = 3840, SB = 13;
  localparam int NPOS = PPC + 2 * (KW / 2);
  localparam int OW   = KH * NPOS * DW;

  logic clk = 0, rst_n = 0;
  always #2 clk = ~clk;

  logic [SB-1:0] cfg_w, cfg_h;
  logic [PPC*DW-1:0] s_tdata;
  logic s_tvalid = 0, s_tuser = 0, s_tlast = 0, s_tready;
  logic [OW-1:0] m_tdata;
  logic m_tvalid, m_tuser, m_tlast, m_yodd, m_tready = 0;

  window_gen #(.DW(DW), .PPC(PPC), .KH(KH), .KW(KW), .MAXW(MAXW), .SB(SB)) dut (
    .clk, .rst_n, .cfg_width(cfg_w), .cfg_height(cfg_h),
    .s_tdata, .s_tvalid, .s_tuser, .s_tlast, .s_tready,
    .m_tdata, .m_tvalid, .m_tuser, .m_tlast, .m_yodd, .m_tready);

  string stim_path, out_path;
  int fi, fo, nout = 0, nexp = 0, seed = 1, vpct = 100, rpct = 100;
  longint cycles = 0;

  // xorshift32: reproducible random numbers on every simulator
  int unsigned rs = 32'h1234_5678;
  function automatic int unsigned rnd100();
    rs ^= rs << 13; rs ^= rs >> 17; rs ^= rs << 5;
    return rs % 100;
  endfunction

  // ---------------------------------------------------------------- sink
  always @(posedge clk) begin
    cycles <= cycles + 1;
    if (rst_n) begin
      if (m_tvalid && m_tready) begin
        $fwrite(fo, "%0d %0d %0d %h\n", m_tuser, m_tlast, m_yodd, m_tdata);
        nout = nout + 1;
      end
      m_tready <= rnd100() < rpct;
    end
  end

  // ---------------------------------------------------------------- source
  // TVALID is only dropped for an idle cycle, never "<= 0" then "<= 1" in
  // the same time step (simulators disagree on which NBA wins there).
  task automatic send(input logic [PPC*DW-1:0] d, input logic u, input logic l);
    while (rnd100() >= vpct) begin
      s_tvalid <= 1'b0;
      @(posedge clk);
    end
    s_tdata <= d; s_tuser <= u; s_tlast <= l; s_tvalid <= 1'b1;
    do @(posedge clk); while (!s_tready);
  endtask

  initial begin
    string tag;
    int w, h, r;
    logic [PPC*DW-1:0] d;
    if (!$value$plusargs("STIM=%s", stim_path)) $fatal(1, "+STIM missing");
    if (!$value$plusargs("OUT=%s", out_path))   $fatal(1, "+OUT missing");
    r = $value$plusargs("NOUT=%d", nexp);
    r = $value$plusargs("SEED=%d", seed);
    r = $value$plusargs("VALID_PCT=%d", vpct);
    r = $value$plusargs("READY_PCT=%d", rpct);
    rs = 32'h9E37_79B9 ^ seed;
    fi = $fopen(stim_path, "r");
    fo = $fopen(out_path, "w");
    cfg_w = 0; cfg_h = 0;
    repeat (5) @(posedge clk);
    rst_n <= 1;
    @(posedge clk);
    while ($fscanf(fi, "%s", tag) == 1) begin
      if (tag == "F") begin
        r = $fscanf(fi, "%d %d", w, h);
        cfg_w = w; cfg_h = h;               // latched by the DUT at SOF
        for (int y = 0; y < h; y++)
          for (int b = 0; b < w / PPC; b++) begin
            r = $fscanf(fi, "%h", d);
            send(d, (y == 0 && b == 0), (b == w / PPC - 1));
          end
      end else if (tag == "J") begin
        r = $fscanf(fi, "%h", d);
        send(d, 1'b0, 1'b0);
      end
    end
    s_tvalid <= 1'b0;
    // wait for all expected output
    while (nout < nexp && cycles < 64'd50_000_000) @(posedge clk);
    repeat (50) @(posedge clk);
    $fclose(fo);
    if (nout != nexp) $display("TB_RESULT FAIL got %0d of %0d beats", nout, nexp);
    else              $display("TB_RESULT DONE %0d beats %0d cycles", nout, cycles);
    $finish;
  end
endmodule
