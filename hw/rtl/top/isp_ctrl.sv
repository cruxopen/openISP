// SPDX-License-Identifier: MIT
//
// isp_ctrl - control / status / interrupt block of isp_top (word 0x40 - 0x4F,
// see CTRL_REGS in hwmodel/regmap.py).
//
// Frame-synchronous configuration: software writes the pending register bank
// (isp_regs), then sets CTRL.COMMIT.  The pending bank is copied to the active
// bank when no frame is inside the pipeline (FRAMES_IN == FRAMES_OUT); while
// a commit is pending the next start of frame is held at the input, so every
// frame is processed with exactly one configuration.  HOLD (level) delays the
// commit while software is still writing; FORCE applies it at once.
// RESET (self-clearing) resets the datapath for 16 cycles (srst) and clears
// the frame counters and a pending commit; the configuration registers and
// tables are kept.  Used at stream on: an aborted stream (e.g. a CSI-2 line
// buffer overflow) can leave a truncated frame in the pipeline, which would
// otherwise keep FRAMES_IN != FRAMES_OUT and hold every later commit.
module isp_ctrl #(
  parameter int MAXW = 3840, SB = 13
) (
  input  logic          clk, rst_n,
  input  logic          wr_en,
  input  logic [15:0]   wr_addr,
  input  logic [31:0]   wr_data,
  input  logic [15:0]   rd_addr,
  output logic [31:0]   rd_data,        // latency 1
  input  logic [SB-1:0] height,         // active frame height
  input  logic          in_sof,         // start of frame accepted at the input
  input  logic          out_eol,        // end of line sent at the output
  input  logic          in_tvalid,
  output logic          hold_sof,       // hold the next SOF back (commit pending)
  output logic          commit,         // copy pending -> active bank
  output logic          srst,           // datapath soft reset (active high)
  output logic          irq
);
  localparam logic [31:0] ID = 32'h4F49_5350, VERSION = {8'd1, 8'd1, 16'(MAXW)};

  logic        pend, hold, force_q;
  logic [2:0]  ist, ien;
  logic [31:0] fin, fout, cframe;
  logic [SB-1:0] oline;
  logic [4:0]  rcnt;

  wire sel   = wr_en && (wr_addr[15:4] == 12'h004);
  wire w_ctl = sel && (wr_addr[3:0] == 4'h2);
  wire idle  = (fin == fout);
  wire fdone = out_eol && (oline == height - 1'b1);

  assign hold_sof = pend && !hold;
  assign commit   = (pend && !hold && idle) || force_q;
  assign srst     = rcnt[4];

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      pend <= 1'b0; hold <= 1'b0; force_q <= 1'b0; rcnt <= '0;
      ist <= '0; ien <= '0; fin <= '0; fout <= '0; cframe <= '0; oline <= '0; irq <= 1'b0;
    end else begin
      force_q <= w_ctl && wr_data[2];
      if (commit) pend <= 1'b0;
      if (w_ctl) begin
        hold <= wr_data[1];
        if (wr_data[0] || wr_data[2]) pend <= !wr_data[2];
      end
      if (commit) cframe <= fin;
      if (in_sof) fin <= fin + 1'b1;
      if (out_eol) oline <= fdone ? '0 : oline + 1'b1;
      if (fdone) fout <= fout + 1'b1;
      // RESET: 16 cycles of datapath reset; counters and pending commit cleared
      if (w_ctl && wr_data[3]) rcnt <= 5'h1f;
      else if (srst) rcnt <= rcnt - 1'b1;
      if (srst) begin
        fin <= '0; fout <= '0; oline <= '0; pend <= 1'b0;
      end
      if (sel && wr_addr[3:0] == 4'h5) ien <= wr_data[2:0];
      // interrupt status: set wins over write-1-to-clear
      ist <= (ist & ~((sel && wr_addr[3:0] == 4'h4) ? wr_data[2:0] : 3'b0))
             | {commit, in_sof, fdone};
      irq <= |(ist & ien);
    end
  end

  always_ff @(posedge clk) begin
    case (rd_addr[3:0])
      4'h0: rd_data <= ID;
      4'h1: rd_data <= VERSION;
      4'h2: rd_data <= {28'd0, srst, 1'b0, hold, pend};
      4'h3: rd_data <= {29'd0, hold_sof && in_tvalid, !idle, pend};
      4'h4: rd_data <= {29'd0, ist};
      4'h5: rd_data <= {29'd0, ien};
      4'h6: rd_data <= fin;
      4'h7: rd_data <= fout;
      4'h8: rd_data <= cframe;
      default: rd_data <= 32'd0;
    endcase
  end
endmodule
