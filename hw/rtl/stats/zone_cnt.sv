// SPDX-License-Identifier: MIT
//
// zone_cnt - position of the current beat in the 16 x 16 statistics grid
// (hwmodel.stats.zone_grid): zone (zy, zx) covers
//   x in [x0 + zx*zw, x0 + (zx+1)*zw), y in [y0 + zy*zh, y0 + (zy+1)*zh).
// Counters only (no division).  2 pixels per beat; x0, y0, zw, zh even.
// Outputs describe the beat presented with `fire` (TUSER = SOF, TLAST = EOL).
module zone_cnt #(
  parameter int SB = 13
) (
  input  logic          clk, rst_n,
  input  logic          fire, tuser, tlast,
  input  logic [SB-1:0] x0, y0, zw, zh,
  output logic          hit,          // pixel pair inside the grid
  output logic [3:0]    zx, zy,
  output logic          zrow_end,     // last row of a zone row (valid with tlast)
  output logic [SB-1:0] y,            // row index
  output logic          yodd
);
  logic [SB-1:0] x_q, xc_q, y_q, yc_q;
  logic [4:0]    zx_q, zy_q;
  logic [SB-1:0] x, xc, yc;
  logic [4:0]    zxx, zyy;
  logic          inx, iny;

  always_comb begin
    x   = tuser ? '0 : x_q;   xc  = tuser ? '0 : xc_q;  zxx = tuser ? '0 : zx_q;
    y   = tuser ? '0 : y_q;   yc  = tuser ? '0 : yc_q;  zyy = tuser ? '0 : zy_q;
    inx = (x >= x0) && !zxx[4];
    iny = (y >= y0) && !zyy[4];
  end
  assign hit      = inx && iny;
  assign zx       = zxx[3:0];
  assign zy       = zyy[3:0];
  assign zrow_end = iny && (yc + 1'b1 == zh);
  assign yodd     = y[0];

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      x_q <= '0; xc_q <= '0; zx_q <= '0; y_q <= '0; yc_q <= '0; zy_q <= '0;
    end else if (fire) begin
      if (tlast) begin
        x_q <= '0; xc_q <= '0; zx_q <= '0;
        y_q <= y + 1'b1;
        yc_q <= yc; zy_q <= zyy;
        if (iny) begin
          if (yc + 1'b1 == zh) begin yc_q <= '0; zy_q <= zyy + 1'b1; end
          else yc_q <= yc + 1'b1;
        end
      end else begin
        x_q <= x + 2'd2;
        xc_q <= xc; zx_q <= zxx;
        y_q <= y; yc_q <= yc; zy_q <= zyy;
        if (inx) begin
          if (xc + 2'd2 == zw) begin xc_q <= '0; zx_q <= zxx + 1'b1; end
          else xc_q <= xc + 2'd2;
        end
      end
    end
  end
endmodule
