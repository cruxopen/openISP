// SPDX-License-Identifier: MIT
//
// window_gen - streaming KH x KW neighbourhood generator with mirror borders.
//
// Input : AXI4-Stream video, PPC pixels per beat, DW bits per pixel,
//         TUSER = start of frame, TLAST = end of line (TLAST is not needed;
//         the line length comes from cfg_width).
// Output: one beat per input beat position. Every beat carries a patch of
//         KH rows x NPOS columns (NPOS = PPC + 2*(KW/2)); output pixel p
//         (0..PPC-1) uses patch columns p .. p+KW-1.
//         m_tdata layout: pixel(row k, col i) = m_tdata[(k*NPOS + i)*DW +: DW],
//         k = 0 is the top row, i = 0 the leftmost column.
//
// Border rule (identical to hwmodel.common.pad / numpy 'reflect'):
//         index -1 -> 1, -2 -> 2, ..., H -> H-2, H+1 -> H-3, ...
// Requirements: cfg_width multiple of PPC, cfg_width >= 3*PPC and > KW/2, cfg_height > KH/2,
//               cfg_width <= MAXW.  The frame size is latched at SOF.
//
// Architecture
//   * 2R line buffers (R = KH/2), read-before-write: the incoming row p is
//     written into slot p mod 2R while the row p-2R stored there is read in
//     the same cycle.  A frame takes H+R row passes; the last R passes replay
//     stored rows (bottom mirror) without consuming input.
//   * per pass, every window row k selects its source (incoming row or a
//     slot) - computed once from the pass index.
//   * a column history of M column vectors; window column i reads
//     hist[offset_i], offset is constant inside the line and changes only
//     within C pixels of the left/right edge.  D = ceil(C/PPC) extra beats per
//     line flush the right edge.
//   * single global-enable pipeline (issue -> data -> window -> FIFO) with
//     credit-based flow control into an output FIFO.

module window_gen #(
  parameter int DW         = 10,
  parameter int PPC        = 2,
  parameter int KH         = 5,
  parameter int KW         = 5,
  parameter int MAXW       = 3840,
  parameter int SB         = 13,             // bits of width/height counters
  parameter int FIFO_DEPTH = 8,
  // derived - do not override
  parameter int NPOS       = PPC + 2 * (KW / 2)
) (
  input  logic                       clk,
  input  logic                       rst_n,
  input  logic [SB-1:0]              cfg_width,
  input  logic [SB-1:0]              cfg_height,

  input  logic [PPC*DW-1:0]          s_tdata,
  input  logic                       s_tvalid,
  input  logic                       s_tuser,
  input  logic                       s_tlast,
  output logic                       s_tready,

  output logic [KH*NPOS*DW-1:0]      m_tdata,
  output logic                       m_tvalid,
  output logic                       m_tuser,
  output logic                       m_tlast,
  output logic                       m_yodd,     // output row index is odd
  input  logic                       m_tready
);

  localparam int R      = KH / 2;
  localparam int C      = KW / 2;
  localparam int NSLOT  = (2 * R > 0) ? 2 * R : 1;
  localparam int D      = (C + PPC - 1) / PPC;    // output lag in beats
  localparam int M      = 2 * C + 2 * PPC;        // column history depth
  localparam int DEPTH  = (MAXW + PPC - 1) / PPC;
  localparam int AW     = (DEPTH > 1) ? $clog2(DEPTH) : 1;
  localparam int SLW    = (NSLOT > 1) ? $clog2(NSLOT) : 1;
  localparam int SELW   = $clog2(NSLOT + 1);      // slot index or INCOMING
  localparam int OFW    = $clog2(M);
  localparam int PPCL   = $clog2(PPC);
  localparam int OW     = KH * NPOS * DW;
  localparam logic [SELW-1:0] SEL_IN = SELW'(NSLOT);
  localparam int FCW    = $clog2(FIFO_DEPTH + 1);

  // pixel PPC must be a power of two
  initial begin
    if ((1 << PPCL) != PPC) $fatal(1, "window_gen: PPC must be a power of two");
    if (KH % 2 == 0 || KW % 2 == 0) $fatal(1, "window_gen: KH/KW must be odd");
    if (FIFO_DEPTH < 6) $fatal(1, "window_gen: FIFO_DEPTH must be >= 6");
  end

  typedef logic signed [SB+2:0] sidx_t;

  // ------------------------------------------------------------------ frame engine
  logic          active;
  logic [SB-1:0] w_l, h_l;
  logic [SB:0]   pass;                // 0 .. H-1+R
  logic [SB-1:0] beat;                // beat inside pass
  logic [SLW-1:0] wslot;              // pass mod NSLOT

  // Effective frame size (latched while active, else the configuration) and
  // per-pass control flags, all kept in registers that are updated together
  // with pass/beat (exact, given cfg_width/cfg_height do not change in the
  // cycle before SOF):
  //   wq, hq   = active ? w_l/h_l : cfg_width/cfg_height
  //   nbl      = w_l / PPC
  //   nbm1     = beats in this pass - 1  (NB, + D for output passes)
  //   pin      = pass < H   (the pass consumes input)
  //   lpass    = pass == H - 1 + R
  logic [SB-1:0] wq, hq, nbl, nbm1;
  logic          pin, lpass;
  wire  [SB-1:0] W  = wq;
  wire  [SB-1:0] H  = hq;
  wire           has_out  = (pass >= R);
  wire           need_in  = pin && (!active || beat < nbl);
  wire           last_beat= active && (beat == nbm1);
  wire           last_pass= lpass;

  // runtime check (simulation): rows must be at least 3 beats long
  always @(posedge clk) begin
    if (rst_n && s_tvalid && s_tuser && !active && (cfg_width >> PPCL) < 3)
      $error("window_gen: cfg_width must be >= %0d", 3 * PPC);
  end

  // flow control: credits = FIFO occupancy + output items in flight
  logic [FCW-1:0] fifo_cnt;
  logic           ov1, ov2, ov3;
  wire  [FCW+1:0] used = fifo_cnt + ov1 + ov2 + ov3;
  wire            space = (used < FIFO_DEPTH - 1);
  wire            adv   = space;

  wire  sof_wait  = !active;
  wire  drop      = sof_wait && s_tvalid && !s_tuser;          // junk before SOF
  wire  issue     = adv && (sof_wait ? (s_tvalid && s_tuser) : (!need_in || s_tvalid));
  assign s_tready = adv && (sof_wait || need_in);

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      wq <= '0; hq <= '0; nbl <= '0; nbm1 <= '1; pin <= 1'b1; lpass <= 1'b0;
    end else if (issue && sof_wait) begin                       // SOF: pass 0 of a new frame
      wq <= cfg_width; hq <= cfg_height; nbl <= cfg_width >> PPCL;
      nbm1 <= (cfg_width >> PPCL) - 1'b1 + ((R == 0) ? SB'(D) : SB'(0));
      pin <= 1'b1; lpass <= 1'b0;
    end else if (issue && last_beat && last_pass) begin         // end of frame
      wq <= cfg_width; hq <= cfg_height; nbm1 <= '1; pin <= 1'b1; lpass <= 1'b0;
    end else if (issue && last_beat) begin                      // next pass
      nbm1 <= nbl - 1'b1 + ((pass + 1'b1 >= R) ? SB'(D) : SB'(0));
      pin   <= ({1'b0, pass} + 1'b1 < {1'b0, h_l});
      lpass <= (pass + 1'b1 == {1'b0, h_l} - 1 + R);
    end else if (!active) begin                                 // idle: follow the configuration
      wq <= cfg_width; hq <= cfg_height;
    end
  end

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      active <= 1'b0; pass <= '0; beat <= '0; wslot <= '0;
      w_l <= '0; h_l <= '0;
    end else if (issue) begin
      if (sof_wait) begin
        active <= 1'b1; w_l <= cfg_width; h_l <= cfg_height;
      end
      if (last_beat) begin
        beat  <= '0;
        wslot <= (wslot == NSLOT - 1) ? '0 : wslot + 1'b1;
        if (last_pass) begin
          pass <= '0; wslot <= '0; active <= 1'b0;
        end else begin
          pass <= pass + 1'b1;
        end
      end else begin
        beat <= beat + 1'b1;
      end
    end
  end

  // ------------------------------------------------------------------ per-beat control (stage 0)
  function automatic sidx_t mirror(input sidx_t v, input sidx_t n);
    if (v < 0)      return -v;
    else if (v > n - 1) return 2 * (n - 1) - v;
    else            return v;
  endfunction

  // Source row of every window row for a pass: slot index or SEL_IN.
  // It changes once per row, so it is computed for the NEXT pass in two
  // pipeline stages and switched in at the last beat of the current pass
  // (requires >= 3 beats per row, i.e. cfg_width >= 3*PPC).
  //   stage a: s = mirror(next_pass - 2R + k)      (source row index)
  //   stage b: rel = next_pass - s, slot = (next_wslot - rel) mod 2R
  sidx_t           srow_a [KH];
  sidx_t           np_a;
  logic [SLW-1:0]  nws_a;
  logic [SELW-1:0] sel_nxt [KH];
  logic [SELW-1:0] sel_cur [KH];
  always_ff @(posedge clk) begin
    sidx_t np, rel, sl;
    // After the last pass the next pass is pass 0 of the next frame; passes
    // < R produce no output, so pass+1 is always good enough.
    np = sidx_t'(pass) + 1;
    // stage a
    np_a  <= np;
    nws_a <= (wslot == NSLOT - 1) ? '0 : wslot + 1'b1;
    for (int k = 0; k < KH; k++)
      srow_a[k] <= mirror(np - 2 * R + k, sidx_t'(H));   // H = cfg_height before SOF
    // stage b
    for (int k = 0; k < KH; k++) begin
      rel = np_a - srow_a[k];                          // 0 .. 2R
      sl  = sidx_t'(nws_a) - rel;
      if (sl < 0) sl = sl + NSLOT;
      sel_nxt[k] <= (rel <= 0 || rel > NSLOT) ? SEL_IN : SELW'(sl);   // <=0/>2R: no-output passes
    end
    if (issue && last_beat)
      sel_cur <= sel_nxt;
  end

  logic [OFW-1:0]  off0 [NPOS];
  always_comb begin
    sidx_t tv, x, src;
    tv = sidx_t'(beat) * PPC + (PPC - 1);            // newest column in history
    for (int i = 0; i < NPOS; i++) begin
      x   = (sidx_t'(beat) - D) * PPC - C + i;
      src = mirror(x, sidx_t'(W));
      off0[i] = OFW'(tv - src);
    end
  end

  // ------------------------------------------------------------------ line buffers
  logic [PPC*DW-1:0] rdata [NSLOT];
  wire  [AW-1:0]     addr = AW'(beat);
  wire               we   = issue && need_in && (R > 0);

  for (genvar g = 0; g < NSLOT; g++) begin : g_slot
    (* ram_style = "block" *) logic [PPC*DW-1:0] mem [DEPTH];
    always_ff @(posedge clk) begin
      if (adv) begin
        rdata[g] <= mem[addr];                       // read-first
        if (we && wslot == g) mem[addr] <= s_tdata;
      end
    end
  end

  // ------------------------------------------------------------------ stage 1: data
  logic              iv1;                            // beat valid (shift history)
  logic [PPC*DW-1:0] din1;
  logic [SELW-1:0]   sel1 [KH];
  logic [OFW-1:0]    off1 [NPOS];
  logic              u1, l1, yo1;

  // ------------------------------------------------------------------ stage 2: history
  logic [DW-1:0]     hist [M][KH];
  logic [OFW-1:0]    off2 [NPOS];
  logic              u2, l2, yo2;

  // ------------------------------------------------------------------ stage 3: window
  logic [OW-1:0]     win3;
  logic              u3, l3, yo3;

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      iv1 <= 1'b0; ov1 <= 1'b0; ov2 <= 1'b0; ov3 <= 1'b0;
    end else if (adv) begin
      // stage 1
      iv1  <= issue;
      ov1  <= issue && has_out && (beat >= D);
      din1 <= s_tdata;
      sel1 <= sel_cur;
      off1 <= off0;
      u1   <= (pass == R) && (beat == D);
      l1   <= last_beat;
      yo1  <= pass[0] ^ R[0];
      // stage 2: shift PPC new columns into the history
      if (iv1) begin
        for (int m = M - 1; m >= PPC; m--) hist[m] <= hist[m - PPC];
        for (int pp = 0; pp < PPC; pp++)
          for (int k = 0; k < KH; k++)
            hist[PPC - 1 - pp][k] <= (sel1[k] == SEL_IN) ? din1[pp*DW +: DW]
                                                         : rdata[sel1[k]][pp*DW +: DW];
      end
      ov2 <= ov1; off2 <= off1; u2 <= u1; l2 <= l1; yo2 <= yo1;
      // stage 3: window select
      for (int k = 0; k < KH; k++)
        for (int i = 0; i < NPOS; i++)
          win3[(k*NPOS + i)*DW +: DW] <= hist[off2[i]][k];
      ov3 <= ov2; u3 <= u2; l3 <= l2; yo3 <= yo2;
    end
  end

  // ------------------------------------------------------------------ output FIFO
  logic [OW+2:0]  fifo [FIFO_DEPTH];
  logic [$clog2(FIFO_DEPTH)-1:0] wp, rp;
  wire push = adv && ov3;
  logic f_valid, f_ready;
  wire pop  = f_valid && f_ready;

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      wp <= '0; rp <= '0; fifo_cnt <= '0;
    end else begin
      if (push) begin
        fifo[wp] <= {yo3, l3, u3, win3};
        wp <= (wp == FIFO_DEPTH - 1) ? '0 : wp + 1'b1;
      end
      if (pop) rp <= (rp == FIFO_DEPTH - 1) ? '0 : rp + 1'b1;
      fifo_cnt <= fifo_cnt + push - pop;
    end
  end

  assign f_valid = (fifo_cnt != 0);

  // registered output (the FIFO is distributed RAM with an asynchronous read)
  axis_skid #(.W(OW + 3)) u_out (
    .clk, .rst_n, .s_data(fifo[rp]), .s_valid(f_valid), .s_ready(f_ready),
    .m_data({m_yodd, m_tlast, m_tuser, m_tdata}), .m_valid(m_tvalid), .m_ready(m_tready));

endmodule
