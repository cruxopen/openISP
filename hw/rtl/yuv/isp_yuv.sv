// SPDX-License-Identifier: MIT
//
// isp_yuv - YUV domain of the ISP:
//   Y : NLM -> BNF -> EE -> BCC        (AF statistics on the NLM input window)
//   UV: elastic FIFO (re-aligned with Y in order) -> FCS (EE edge map) -> HSC
// Input/output beat: {V, U, Y} per pixel, PPC pixels.
module isp_yuv #(
  parameter int PPC = 2, MAXW = 3840, SB = 13,
  // UV FIFO: must hold the beats in flight in the Y chain (about 6 lines)
  parameter int UVD = 1 << $clog2(7 * MAXW / PPC + 512)
) (
  input  logic              clk, rst_n,
  input  logic [SB-1:0]     cfg_width, cfg_height,
  // NLM
  input  logic [2:0]        nlm_dshift,
  input  logic              nlm_lut_we,
  input  logic [7:0]        nlm_lut_addr, nlm_lut_data,
  // BNF
  input  logic [25*12-1:0]  bnf_dw,
  input  logic [4*8-1:0]    bnf_rw,
  input  logic [3*8-1:0]    bnf_rthres,
  // EE
  input  logic [15*8-1:0]   ee_filt,
  input  logic [2*10-1:0]   ee_gain,
  input  logic [2*16-1:0]   ee_thr,
  input  logic [2*10-1:0]   ee_emclip,
  // FCS / HSC / BCC
  input  logic [10:0]       fcs_e0, fcs_e1,
  input  logic [8:0]        fcs_gain,
  input  logic signed [9:0] hsc_sin, hsc_cos,
  input  logic [9:0]        hsc_sat,
  input  logic signed [8:0] bright,
  input  logic signed [7:0] contrast,
  // AF statistics
  input  logic [SB-1:0]     st_x0, st_y0, st_zw, st_zh,
  input  logic [8:0]        af_coring,
  input  logic [11:0]       af_rd_addr,
  output logic [31:0]       af_rd_data,
  // stream
  input  logic [PPC*24-1:0] s_tdata,
  input  logic              s_tvalid, s_tuser, s_tlast,
  output logic              s_tready,
  output logic [PPC*24-1:0] m_tdata,
  output logic              m_tvalid, m_tuser, m_tlast,
  input  logic              m_tready
);
  logic [PPC*8-1:0]  y_in, y_nlm, y_bnf;
  logic [PPC*16-1:0] uv_in, uv_out;
  logic [PPC*20-1:0] y_ee;
  for (genvar p = 0; p < PPC; p++) begin : g_split
    assign y_in[p*8 +: 8]   = s_tdata[p*24 +: 8];
    assign uv_in[p*16 +: 16] = s_tdata[p*24 + 8 +: 16];
  end

  logic nlm_vi, nlm_ri, uv_ri;
  assign nlm_vi   = s_tvalid && uv_ri;
  assign s_tready = nlm_ri && uv_ri;

  logic nv, nu, nl, nr, bv, bu, bl, br, ev, eu, el, er;
  logic [3*(PPC+2)*8-1:0] af_win;
  logic af_fire, af_user, af_last;

  isp_nlm #(.PPC(PPC), .MAXW(MAXW), .SB(SB)) u_nlm (
    .clk, .rst_n, .cfg_width, .cfg_height, .dshift(nlm_dshift),
    .lut_we(nlm_lut_we), .lut_addr(nlm_lut_addr), .lut_data(nlm_lut_data),
    .s_tdata(y_in), .s_tvalid(nlm_vi), .s_tuser, .s_tlast, .s_tready(nlm_ri),
    .m_tdata(y_nlm), .m_tvalid(nv), .m_tuser(nu), .m_tlast(nl), .m_tready(nr),
    .af_win, .af_fire, .af_user, .af_last);

  stats_af #(.PPC(PPC), .SB(SB)) u_af (
    .clk, .rst_n, .cfg_height, .x0(st_x0), .y0(st_y0), .zw(st_zw), .zh(st_zh), .coring(af_coring),
    .win3(af_win), .fire(af_fire), .tuser(af_user), .tlast(af_last),
    .rd_addr(af_rd_addr), .rd_data(af_rd_data));

  isp_bnf #(.PPC(PPC), .MAXW(MAXW), .SB(SB)) u_bnf (
    .clk, .rst_n, .cfg_width, .cfg_height, .dw(bnf_dw), .rw(bnf_rw), .rthres(bnf_rthres),
    .s_tdata(y_nlm), .s_tvalid(nv), .s_tuser(nu), .s_tlast(nl), .s_tready(nr),
    .m_tdata(y_bnf), .m_tvalid(bv), .m_tuser(bu), .m_tlast(bl), .m_tready(br));

  isp_ee #(.PPC(PPC), .MAXW(MAXW), .SB(SB)) u_ee (
    .clk, .rst_n, .cfg_width, .cfg_height, .filt(ee_filt), .gain(ee_gain), .thr(ee_thr),
    .emclip(ee_emclip),
    .s_tdata(y_bnf), .s_tvalid(bv), .s_tuser(bu), .s_tlast(bl), .s_tready(br),
    .m_tdata(y_ee), .m_tvalid(ev), .m_tuser(eu), .m_tlast(el), .m_tready(er));

  logic uv_vo, pr;
  fifo_fwft #(.W(PPC*16), .DEPTH(UVD), .STYLE("ultra")) u_uv (
    .clk, .rst_n, .s_data(uv_in), .s_valid(s_tvalid && nlm_ri), .s_ready(uv_ri),
    .m_data(uv_out), .m_valid(uv_vo), .m_ready(ev && pr));
  assign er = pr && uv_vo;

  isp_yuvpost #(.PPC(PPC)) u_post (
    .clk, .rst_n, .fcs_e0, .fcs_e1, .fcs_gain, .hsc_sin, .hsc_cos, .hsc_sat, .bright, .contrast,
    .s_tdata({uv_out, y_ee}), .s_tvalid(ev && uv_vo), .s_tuser(eu), .s_tlast(el), .s_tready(pr),
    .m_tdata, .m_tvalid, .m_tuser, .m_tlast, .m_tready);
endmodule
