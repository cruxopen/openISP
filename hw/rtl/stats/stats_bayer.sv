// SPDX-License-Identifier: MIT
//
// stats_bayer - AWB / AE statistics on the Bayer stream (hwmodel.stats.bayer_stats).
// Passive observer of an AXI4-Stream (never stalls it), PPC = 2: one 2x2
// quad per beat of every odd row (the even row is kept in a line buffer).
//   per zone: awb_r, awb_g (Gr+Gb), awb_b, awb_n over valid quads
//             (max < awb_sat and min >= awb_dark), ae_sum of L = (R+Gr+Gb+B+2)>>2
//   global  : 256-bin histogram of L >> 2
// Double buffered: frame k is written to bank k%2; the readout shows the
// last completed frame.  Read map (word address, latency 2):
//   0x000 awb_r[256] 0x100 awb_g 0x200 awb_b 0x300 awb_n 0x400 ae_sum
//   0x500 hist[256]  0x600 number of completed frames
module stats_bayer #(
  parameter int DW = 10, PPC = 2, MAXW = 3840, SB = 13
) (
  input  logic              clk, rst_n,
  input  logic [SB-1:0]     cfg_height,
  input  logic [7:0]        cfa,
  input  logic [SB-1:0]     x0, y0, zw, zh,
  input  logic [DW-1:0]     awb_sat, awb_dark,
  input  logic [PPC*DW-1:0] tdata,
  input  logic              fire, tuser, tlast,
  input  logic [11:0]       rd_addr,
  output logic [31:0]       rd_data
);
  initial if (PPC != 2) $fatal(1, "stats_bayer: PPC must be 2");
  localparam int LBD = MAXW / 2;

  // ---------------------------------------------------------------- s0: position
  logic hit, zrow_end, yodd;
  logic [3:0] zx, zy;
  logic [SB-1:0] y, h_l, xb;
  zone_cnt #(.SB(SB)) u_zc (.clk, .rst_n, .fire, .tuser, .tlast, .x0, .y0, .zw, .zh,
                            .hit, .zx, .zy, .zrow_end, .y, .yodd);
  always_ff @(posedge clk) begin
    if (!rst_n) xb <= '0;
    else if (fire) xb <= tlast ? '0 : (tuser ? SB'(1) : xb + 1'b1);
    if (fire && tuser) h_l <= cfg_height;
  end
  wire [SB-1:0] beat = tuser ? '0 : xb;
  wire [SB-1:0] hh   = tuser ? cfg_height : h_l;

  // even row line buffer
  (* ram_style = "block" *) logic [2*DW-1:0] lb [LBD];
  logic [2*DW-1:0] top1;
  always_ff @(posedge clk) begin
    if (fire && !yodd) lb[beat] <= tdata;
    top1 <= lb[beat];
  end

  // ---------------------------------------------------------------- s1
  logic          v1, fl1, fe1;
  logic [3:0]    zx1, zy1;
  logic [2*DW-1:0] bot1;
  always_ff @(posedge clk) begin
    if (!rst_n) begin v1 <= 1'b0; fl1 <= 1'b0; fe1 <= 1'b0; end
    else begin
      v1  <= fire && yodd && hit;
      fl1 <= fire && yodd && tlast && zrow_end;
      fe1 <= fire && tlast && (y == hh - 1'b1);
    end
    zx1 <= zx; zy1 <= zy; bot1 <= tdata;
  end

  // s1b: block-RAM output register
  logic          v1b, fl1b, fe1b;
  logic [3:0]    zx1b, zy1b;
  logic [2*DW-1:0] top1b, bot1b;
  always_ff @(posedge clk) begin
    if (!rst_n) begin v1b <= 1'b0; fl1b <= 1'b0; fe1b <= 1'b0; end
    else begin v1b <= v1; fl1b <= fl1; fe1b <= fe1; end
    zx1b <= zx1; zy1b <= zy1; top1b <= top1; bot1b <= bot1;
  end

  // ---------------------------------------------------------------- s2: quad
  logic          v2, q2, fl2, fe2;
  logic [3:0]    zx2, zy2;
  logic [DW-1:0] r2, b2, l2;
  logic [DW:0]   g2;
  always_ff @(posedge clk) begin
    logic [DW-1:0] pv [4];
    logic [DW-1:0] mx, mn;
    logic [DW+1:0] s;
    logic [DW-1:0] r, b;
    logic [DW:0]   g;
    pv[0] = top1b[DW-1:0]; pv[1] = top1b[2*DW-1:DW];
    pv[2] = bot1b[DW-1:0]; pv[3] = bot1b[2*DW-1:DW];
    mx = pv[0]; mn = pv[0]; s = '0; r = '0; b = '0; g = '0;
    for (int k = 0; k < 4; k++) begin
      if (pv[k] > mx) mx = pv[k];
      if (pv[k] < mn) mn = pv[k];
      s += pv[k];
      case (cfa[2*k +: 2])
        2'd0: r = pv[k];
        2'd3: b = pv[k];
        default: g += pv[k];
      endcase
    end
    if (!rst_n) begin v2 <= 1'b0; fl2 <= 1'b0; fe2 <= 1'b0; end
    else begin v2 <= v1b; fl2 <= fl1b; fe2 <= fe1b; end
    q2 <= (mx < awb_sat) && (mn >= awb_dark);
    r2 <= r; g2 <= g; b2 <= b;
    l2 <= DW'((s + 2) >> 2);
    zx2 <= zx1b; zy2 <= zy1b;
  end

  // ---------------------------------------------------------------- frame id
  // fid: frame being collected; every in-flight write carries its own bank,
  // so the next frame can start while the previous one is still draining.
  logic [1:0]  fid, pub;           // zone bank = id[0]
  logic [1:0]  hb, hpub;           // histogram bank 0..2 (current, published)
  logic [31:0] nframes;
  logic        pend;
  logic [1:0]  pend_id, pend_hb;
  logic [2:0]  pdly;
  logic        zbusy;
  always_ff @(posedge clk) begin
    if (!rst_n) begin
      fid <= '0; pub <= '0; hb <= '0; hpub <= 2'd2; nframes <= '0; pend <= 1'b0; pdly <= '0;
    end else begin
      if (fe2) begin
        fid <= fid + 1'b1; hb <= (hb == 2'd2) ? 2'd0 : hb + 1'b1;
        pend <= 1'b1; pend_id <= fid; pend_hb <= hb; pdly <= 3'd4;
      end else if (pdly != 0) pdly <= pdly - 1'b1;
      else if (pend && !zbusy) begin
        pend <= 1'b0; pub <= pend_id; hpub <= pend_hb; nframes <= nframes + 1'b1;
      end
    end
  end

  // ---------------------------------------------------------------- zones
  logic        zwe;
  logic [8:0]  zwa;
  logic [5*32-1:0] zwd;
  zone_acc #(.NS(5), .VW(16)) u_acc (
    .clk, .rst_n, .add(v2), .zx(zx2),
    .vals({16'(l2), 16'(q2), 16'(q2 ? b2 : '0), 16'(q2 ? g2 : '0), 16'(q2 ? r2 : '0)}),
    .flush(fl2), .zy(zy2), .fid, .wr_en(zwe), .wr_addr(zwa), .wr_data(zwd), .busy(zbusy));

  logic [11:0] ra;
  logic [31:0] zrd [5];
  always_ff @(posedge clk) ra <= rd_addr;
  for (genvar s = 0; s < 5; s++) begin : g_zm
    (* ram_style = "block" *) logic [31:0] zmem [512];
    always_ff @(posedge clk) begin
      if (zwe) zmem[zwa] <= zwd[s*32 +: 32];
      zrd[s] <= zmem[{pub[0], ra[7:0]}];
    end
  end

  // ---------------------------------------------------------------- histogram
  // 3 banks: hb is written (read-modify-write with forwarding), hpub is
  // published, the third one is cleared in idle cycles during the frame.
  // hmem (RMW) and hcpu_m (readout) are identical copies.
  (* ram_style = "block" *) logic [31:0] hmem   [1024];
  (* ram_style = "block" *) logic [31:0] hcpu_m [1024];
  initial for (int i = 0; i < 1024; i++) begin hmem[i] = '0; hcpu_m[i] = '0; end

  logic        hv3, hfw;
  logic [9:0]  ha3;
  logic [31:0] hnew_q, hrd, hcpu;
  logic [8:0]  ccnt;                         // clear counter (bit 8 = done)
  logic [1:0]  cbank;
  wire  [9:0]  ha2 = {hb, l2[DW-1:DW-8]};
  always_ff @(posedge clk) begin
    hrd <= hmem[ha2];
    if (!rst_n) hv3 <= 1'b0; else hv3 <= v2;
    ha3 <= ha2;
    hfw <= v2 && hv3 && (ha2 == ha3);        // read collides with the write below
  end
  wire [31:0] hnew = (hfw ? hnew_q : hrd) + 1'b1;
  wire        hcw  = !hv3 && !ccnt[8];       // clear write slot
  wire [9:0]  hwa  = hv3 ? ha3 : {cbank, ccnt[7:0]};
  wire [31:0] hwd  = hv3 ? hnew : 32'd0;
  always_ff @(posedge clk) begin
    if (hv3 || hcw) begin hmem[hwa] <= hwd; hcpu_m[hwa] <= hwd; end
    if (hv3) hnew_q <= hnew;
    hcpu <= hcpu_m[{hpub, ra[7:0]}];
  end
  always_ff @(posedge clk) begin
    if (!rst_n) begin ccnt <= 9'h100; cbank <= 2'd1; end
    else if (fire && tuser) begin               // new frame uses hb: clear the bank after it
      ccnt <= '0; cbank <= (hb == 2'd2) ? 2'd0 : hb + 1'b1;
    end else if (hcw) ccnt <= ccnt + 1'b1;
  end

  // ---------------------------------------------------------------- readout
  logic [11:0] ra2;
  always_ff @(posedge clk) ra2 <= ra;
  always_comb begin
    case (ra2[11:8])
      4'h0, 4'h1, 4'h2, 4'h3, 4'h4: rd_data = zrd[ra2[10:8]];
      4'h5:    rd_data = hcpu;
      4'h6:    rd_data = nframes;
      default: rd_data = 32'd0;
    endcase
  end
endmodule
