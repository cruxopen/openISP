// SPDX-License-Identifier: MIT
//
// isp_yuvpost - point-wise YUV stage: FCS (uses the EE edge map), HSC on UV,
// BCC on Y (hwmodel.blocks.fcs / hsc / bcc).
// Input beat : [PPC*8-1:0] Y, [PPC*12] edge map (signed), [PPC*16] {V, U} per pixel
// Output beat: {V, U, Y} per pixel (24 bits).
//   FCS: g = |em| <= e0 ? gain : |em| >= e1 ? 0 : (gain * (e1 - |em|)) >> log2(e1 - e0)
//        U' = clip(rshr((U - 128) * g, 8) + 128)      (e1 - e0 power of two, e1 <= 2047)
//   HSC: u1 = rshr(u*cos + v*sin, 8), v1 = rshr(v*cos - u*sin, 8),
//        U'' = clip(rshr(u1 * sat, 8) + 128)           (sin/cos Q8 from the hue table)
//   BCC: Y' = clip(Y + bright + rshr((Y - 128) * contrast, 5))
module isp_yuvpost #(
  parameter int PPC = 2
) (
  input  logic               clk, rst_n,
  input  logic [10:0]        fcs_e0, fcs_e1,
  input  logic [8:0]         fcs_gain,
  input  logic signed [9:0]  hsc_sin, hsc_cos,
  input  logic [9:0]         hsc_sat,
  input  logic signed [8:0]  bright,
  input  logic signed [7:0]  contrast,
  input  logic [PPC*36-1:0]  s_tdata,
  input  logic               s_tvalid, s_tuser, s_tlast,
  output logic               s_tready,
  output logic [PPC*24-1:0]  m_tdata,
  output logic               m_tvalid, m_tuser, m_tlast,
  input  logic               m_tready
);
  logic [PPC*24-1:0] od;
  logic rdy;
  localparam int LAT = 9;
  logic en;
  assign s_tready = rdy;
  assign en = 1'b1;                       // free-running pipeline
  pipe_out #(.L(LAT), .W(PPC*24), .SW(2)) u_pipe (
    .clk, .rst_n, .in_valid(s_tvalid), .in_side({s_tuser, s_tlast}), .in_ready(rdy),
    .od, .m_tdata, .m_tvalid, .m_side({m_tuser, m_tlast}), .m_tready);

  // log2 of the FCS ramp length (static configuration)
  logic [3:0] fk;
  always_comb begin
    logic [10:0] span;
    span = fcs_e1 - fcs_e0;
    fk = '0;
    for (int i = 0; i < 11; i++) if (span[i]) fk = 4'(i);
  end

  function automatic logic [7:0] clip8(logic signed [31:0] v);
    return (v < 0) ? 8'd0 : (v > 255) ? 8'd255 : v[7:0];
  endfunction

  for (genvar p = 0; p < PPC; p++) begin : g_px
    wire [7:0]         y0 = s_tdata[p*8 +: 8];
    wire signed [11:0] e0 = s_tdata[PPC*8 + p*12 +: 12];
    wire [7:0]         u0 = s_tdata[PPC*20 + p*16 +: 8];
    wire [7:0]         v0 = s_tdata[PPC*20 + p*16 + 8 +: 8];

    // s1: |em| region, BCC product
    logic [1:0]  rg1;                       // 0: gain, 1: ramp, 2: zero
    logic [10:0] df1;
    logic [7:0]  y1, u1, v1;
    logic signed [16:0] bp1;
    always_ff @(posedge clk) begin
      if (en) begin
        logic [11:0] a;
        a = (e0 < 0) ? 12'(-e0) : 12'(e0);
        rg1 <= (a <= fcs_e0) ? 2'd0 : (a >= fcs_e1) ? 2'd2 : 2'd1;
        df1 <= 11'(fcs_e1 - a);
        bp1 <= ($signed({1'b0, y0}) - 9'sd128) * contrast;
        y1 <= y0; u1 <= u0; v1 <= v0;
      end
    end
    // s2: FCS ramp product, BCC result
    logic [19:0] r2;
    logic [1:0]  rg2;
    logic [7:0]  y2a, u2a, v2a;
    always_ff @(posedge clk) begin
      if (en) begin
        r2  <= fcs_gain * df1;
        rg2 <= rg1;
        y2a <= clip8($signed({1'b0, y1}) + bright + ((bp1 + 16) >>> 5));
        u2a <= u1; v2a <= v1;
      end
    end
    // s2b: FCS gain
    logic [8:0] g2;
    logic [7:0] y2, u2, v2;
    always_ff @(posedge clk) begin
      if (en) begin
        logic [19:0] r;
        r = r2 >> fk;
        g2 <= (rg2 == 2'd0) ? fcs_gain : (rg2 == 2'd2) ? 9'd0 : r[8:0];
        y2 <= y2a; u2 <= u2a; v2 <= v2a;
      end
    end
    // s3: FCS products (+ rounding), s3b: shift, offset, clip
    logic signed [18:0] pu3, pv3;
    logic [7:0] y3a;
    always_ff @(posedge clk) begin
      if (en) begin
        pu3 <= ($signed({1'b0, u2}) - 9'sd128) * $signed({1'b0, g2}) + 128;
        pv3 <= ($signed({1'b0, v2}) - 9'sd128) * $signed({1'b0, g2}) + 128;
        y3a <= y2;
      end
    end
    logic [7:0] y3, u3, v3;
    always_ff @(posedge clk) begin
      if (en) begin
        u3 <= clip8((pu3 >>> 8) + 128);
        v3 <= clip8((pv3 >>> 8) + 128);
        y3 <= y3a;
      end
    end
    // s4: rotation products
    logic signed [19:0] uc4, vs4, vc4, us4;
    logic [7:0] y4;
    always_ff @(posedge clk) begin
      if (en) begin
        logic signed [8:0] u, v;
        u = $signed({1'b0, u3}) - 9'sd128;
        v = $signed({1'b0, v3}) - 9'sd128;
        uc4 <= u * hsc_cos; vs4 <= v * hsc_sin; vc4 <= v * hsc_cos; us4 <= u * hsc_sin;
        y4 <= y3;
      end
    end
    // s5: rotated
    logic signed [12:0] ur5, vr5;
    logic [7:0] y5;
    always_ff @(posedge clk) begin
      if (en) begin
        logic signed [20:0] a, b;
        a = (uc4 + vs4 + 128) >>> 8;
        b = (vc4 - us4 + 128) >>> 8;
        ur5 <= 13'(a); vr5 <= 13'(b);
        y5 <= y4;
      end
    end
    // s6: saturation products
    logic signed [23:0] us6, vs6;
    logic [7:0] y6;
    always_ff @(posedge clk) begin
      if (en) begin
        us6 <= ur5 * $signed({1'b0, hsc_sat});
        vs6 <= vr5 * $signed({1'b0, hsc_sat});
        y6 <= y5;
      end
    end
    // s7: output
    always_ff @(posedge clk) begin
      if (en) begin
        od[p*24 +: 24] <= {clip8(((vs6 + 128) >>> 8) + 128), clip8(((us6 + 128) >>> 8) + 128), y6};
      end
    end
  end
endmodule
