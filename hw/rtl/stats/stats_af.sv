// SPDX-License-Identifier: MIT
//
// stats_af - AF statistics (hwmodel.stats.af_stats) on Y, observer of a
// 3-row window stream (mirror borders come from the window generator; in
// the ISP it is the centre of the NLM 7x7 window).
//   g = |Y(x+1,y) - Y(x-1,y)| + |Y(x,y+1) - Y(x,y-1)|,  af[z] = sum (g > coring ? g : 0)
// win3: 3 rows x (PPC+2) columns, pixel(row k, col i) at [(k*(PPC+2) + i)*8 +: 8].
// Read map (latency 2): 0x000 af[256], 0x100 number of completed frames.
module stats_af #(
  parameter int PPC = 2, SB = 13
) (
  input  logic                  clk, rst_n,
  input  logic [SB-1:0]         cfg_height,
  input  logic [SB-1:0]         x0, y0, zw, zh,
  input  logic [8:0]            coring,
  input  logic [3*(PPC+2)*8-1:0] win3,
  input  logic                  fire, tuser, tlast,
  input  logic [11:0]           rd_addr,
  output logic [31:0]           rd_data
);
  initial if (PPC != 2) $fatal(1, "stats_af: PPC must be 2");
  localparam int NP = PPC + 2;

  logic hit, zrow_end, yodd;
  logic [3:0] zx, zy;
  logic [SB-1:0] y, h_l;
  zone_cnt #(.SB(SB)) u_zc (.clk, .rst_n, .fire, .tuser, .tlast, .x0, .y0, .zw, .zh,
                            .hit, .zx, .zy, .zrow_end, .y, .yodd);
  always_ff @(posedge clk) if (fire && tuser) h_l <= cfg_height;
  wire [SB-1:0] hh = tuser ? cfg_height : h_l;

  function automatic logic [8:0] absd(logic [7:0] a, logic [7:0] b);
    return (a > b) ? 9'(a - b) : 9'(b - a);
  endfunction

  // s1: gradients
  logic        v1, fl1, fe1;
  logic [3:0]  zx1, zy1;
  logic [9:0]  g1 [PPC];
  always_ff @(posedge clk) begin
    if (!rst_n) begin v1 <= 1'b0; fl1 <= 1'b0; fe1 <= 1'b0; end
    else begin
      v1  <= fire && hit;
      fl1 <= fire && tlast && zrow_end;
      fe1 <= fire && tlast && (y == hh - 1'b1);
    end
    zx1 <= zx; zy1 <= zy;
    for (int p = 0; p < PPC; p++)
      g1[p] <= absd(win3[(1*NP + p + 2)*8 +: 8], win3[(1*NP + p)*8 +: 8]) +
               absd(win3[(2*NP + p + 1)*8 +: 8], win3[(0*NP + p + 1)*8 +: 8]);
  end

  // s2: coring, pair sum
  logic        v2, fl2, fe2;
  logic [3:0]  zx2, zy2;
  logic [10:0] s2;
  always_ff @(posedge clk) begin
    logic [10:0] s;
    s = '0;
    for (int p = 0; p < PPC; p++) if (g1[p] > {1'b0, coring}) s += g1[p];
    if (!rst_n) begin v2 <= 1'b0; fl2 <= 1'b0; fe2 <= 1'b0; end
    else begin v2 <= v1; fl2 <= fl1; fe2 <= fe1; end
    zx2 <= zx1; zy2 <= zy1; s2 <= s;
  end

  logic [1:0]  fid, pub, pend_id;
  logic [31:0] nframes;
  logic        pend, zbusy;
  logic [2:0]  pdly;
  always_ff @(posedge clk) begin
    if (!rst_n) begin fid <= '0; pub <= '0; nframes <= '0; pend <= 1'b0; pdly <= '0; end
    else begin
      if (fe2) begin fid <= fid + 1'b1; pend <= 1'b1; pend_id <= fid; pdly <= 3'd4; end
      else if (pdly != 0) pdly <= pdly - 1'b1;
      else if (pend && !zbusy) begin pend <= 1'b0; pub <= pend_id; nframes <= nframes + 1'b1; end
    end
  end

  logic        zwe;
  logic [8:0]  zwa;
  logic [31:0] zwd;
  zone_acc #(.NS(1), .VW(11)) u_acc (
    .clk, .rst_n, .add(v2), .zx(zx2), .vals(s2), .flush(fl2), .zy(zy2), .fid,
    .wr_en(zwe), .wr_addr(zwa), .wr_data(zwd), .busy(zbusy));

  (* ram_style = "block" *) logic [31:0] zmem [512];
  logic [11:0] ra, ra2;
  logic [31:0] zrd;
  always_ff @(posedge clk) begin
    ra <= rd_addr; ra2 <= ra;
    if (zwe) zmem[zwa] <= zwd;
    zrd <= zmem[{pub[0], ra[7:0]}];
  end
  assign rd_data = (ra2[11:8] == 4'h0) ? zrd : (ra2[11:8] == 4'h1) ? nframes : 32'd0;
endmodule
