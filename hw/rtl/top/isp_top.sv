// SPDX-License-Identifier: MIT
//
// isp_top - openISP hardware pipeline (bit-exact with hwmodel.pipeline.run)
//
//   RAW10 -> DPC -> BLC -> AAF -> AWB -> CNF -> CFA -> CCM -> Gamma -> CSC -> YUV -> YUV444
//                    |                                                  (NLM BNF EE | FCS HSC BCC)
//                    +-> AWB/AE statistics                 AF statistics on the NLM input
//
// PPC = 2 pixels per beat.  AXI4-Stream video in/out (TUSER = SOF, TLAST = EOL),
// a register slice between all blocks.  Register bus (word addresses, see
// hwmodel/regmap.py), read latency 2.  Configuration registers are double
// buffered and take effect at a frame boundary after CTRL.COMMIT (isp_ctrl);
// the gamma / NLM tables are written directly (load them while stopped).
//   in : {p1, p0} RAW10      out: {V1, U1, Y1, V0, U0, Y0}
module isp_top #(
  parameter int MAXW = 3840, SB = 13
) (
  input  logic        clk, rst_n,
  input  logic        wr_en,
  input  logic [15:0] wr_addr,
  input  logic [31:0] wr_data,
  input  logic [15:0] rd_addr,
  output logic [31:0] rd_data,
  input  logic [19:0] s_tdata,
  input  logic        s_tvalid, s_tuser, s_tlast,
  output logic        s_tready,
  output logic [47:0] m_tdata,
  output logic        m_tvalid, m_tuser, m_tlast,
  input  logic        m_tready,
  output logic        irq
);
  localparam int PPC = 2;

  // ------------------------------------------------------------------ registers
  logic [31:0] reg_rd, sb_rd, af_rd;
  logic [12:0] width, height, st_x0, st_y0, st_zw, st_zh;
  logic [7:0]  cfa;
  logic [9:0]  dpc_thres, bl_r, bl_gr, bl_gb, bl_b, cnf_thres, awb_sat, awb_dark, ee_g0, ee_g1, hsc_sat;
  logic        dpc_mean;
  logic signed [9:0] bl_alpha, bl_beta, ee_clo, ee_chi, hsc_sin, hsc_cos;
  logic [13:0] awb_r, awb_gr, awb_gb, awb_b;
  logic signed [15:0] ccm_m0, ccm_m1, ccm_m2, ccm_m3, ccm_m4, ccm_m5, ccm_m6, ccm_m7, ccm_m8;
  logic signed [15:0] csc_m0, csc_m1, csc_m2, csc_m3, csc_m4, csc_m5, csc_m6, csc_m7, csc_m8;
  logic signed [21:0] ccm_o0, ccm_o1, ccm_o2, csc_o0, csc_o1, csc_o2;
  logic [2:0]  nlm_dshift;
  logic [11:0] bnf_dw [25];
  logic [7:0]  bnf_rw0, bnf_rw1, bnf_rw2, bnf_rw3, bnf_t0, bnf_t1, bnf_t2;
  logic signed [7:0] ee_f [15];
  logic [15:0] ee_t0, ee_t1;
  logic [10:0] fcs_e0, fcs_e1;
  logic [8:0]  fcs_gain, af_coring;
  logic signed [8:0] bright;
  logic signed [7:0] contrast;

  logic [31:0] ctrl_rd;
  logic        commit, hold_sof, dpc_ready, srst, rst_dp_n;
  wire         gate = hold_sof && s_tuser;            // hold the SOF beat for a commit
  assign       s_tready = dpc_ready && !gate && rst_dp_n;
  // datapath reset: power-on reset or CTRL.RESET (registers and tables are kept)
  always_ff @(posedge clk) rst_dp_n <= rst_n && !srst;

  isp_ctrl #(.MAXW(MAXW), .SB(SB)) u_ctrl (
    .clk, .rst_n, .wr_en, .wr_addr, .wr_data, .rd_addr, .rd_data(ctrl_rd), .height,
    .in_sof(s_tvalid && s_tready && s_tuser), .out_eol(m_tvalid && m_tready && m_tlast),
    .in_tvalid(s_tvalid), .hold_sof, .commit, .srst, .irq);

  isp_regs u_regs (
    .clk, .rst_n, .commit, .wr_en, .wr_addr, .wr_data, .rd_addr, .rd_data(reg_rd),
    .width, .height, .cfa, .dpc_thres, .dpc_mean, .bl_r, .bl_gr, .bl_gb, .bl_b, .bl_alpha, .bl_beta,
    .awb_r, .awb_gr, .awb_gb, .awb_b, .cnf_thres,
    .ccm_m0, .ccm_m1, .ccm_m2, .ccm_m3, .ccm_m4, .ccm_m5, .ccm_m6, .ccm_m7, .ccm_m8,
    .ccm_o0, .ccm_o1, .ccm_o2,
    .csc_m0, .csc_m1, .csc_m2, .csc_m3, .csc_m4, .csc_m5, .csc_m6, .csc_m7, .csc_m8,
    .csc_o0, .csc_o1, .csc_o2, .nlm_dshift,
    .bnf_dw0(bnf_dw[0]), .bnf_dw1(bnf_dw[1]), .bnf_dw2(bnf_dw[2]), .bnf_dw3(bnf_dw[3]),
    .bnf_dw4(bnf_dw[4]), .bnf_dw5(bnf_dw[5]), .bnf_dw6(bnf_dw[6]), .bnf_dw7(bnf_dw[7]),
    .bnf_dw8(bnf_dw[8]), .bnf_dw9(bnf_dw[9]), .bnf_dw10(bnf_dw[10]), .bnf_dw11(bnf_dw[11]),
    .bnf_dw12(bnf_dw[12]), .bnf_dw13(bnf_dw[13]), .bnf_dw14(bnf_dw[14]), .bnf_dw15(bnf_dw[15]),
    .bnf_dw16(bnf_dw[16]), .bnf_dw17(bnf_dw[17]), .bnf_dw18(bnf_dw[18]), .bnf_dw19(bnf_dw[19]),
    .bnf_dw20(bnf_dw[20]), .bnf_dw21(bnf_dw[21]), .bnf_dw22(bnf_dw[22]), .bnf_dw23(bnf_dw[23]),
    .bnf_dw24(bnf_dw[24]),
    .bnf_rw0, .bnf_rw1, .bnf_rw2, .bnf_rw3, .bnf_t0, .bnf_t1, .bnf_t2,
    .ee_f0(ee_f[0]), .ee_f1(ee_f[1]), .ee_f2(ee_f[2]), .ee_f3(ee_f[3]), .ee_f4(ee_f[4]),
    .ee_f5(ee_f[5]), .ee_f6(ee_f[6]), .ee_f7(ee_f[7]), .ee_f8(ee_f[8]), .ee_f9(ee_f[9]),
    .ee_f10(ee_f[10]), .ee_f11(ee_f[11]), .ee_f12(ee_f[12]), .ee_f13(ee_f[13]), .ee_f14(ee_f[14]),
    .ee_g0, .ee_g1, .ee_t0, .ee_t1, .ee_clo, .ee_chi, .fcs_e0, .fcs_e1, .fcs_gain,
    .hsc_sin, .hsc_cos, .hsc_sat, .bright, .contrast, .st_x0, .st_y0, .st_zw, .st_zh,
    .awb_sat, .awb_dark, .af_coring);

  logic [25*12-1:0] bnf_dw_v;
  logic [15*8-1:0]  ee_f_v;
  for (genvar t = 0; t < 25; t++) begin : g_dw assign bnf_dw_v[t*12 +: 12] = bnf_dw[t]; end
  for (genvar t = 0; t < 15; t++) begin : g_ef assign ee_f_v[t*8 +: 8] = ee_f[t]; end

  wire gamma_we = wr_en && (wr_addr[15:10] == 6'b000001);   // 0x0400 - 0x07FF
  wire nlm_we   = wr_en && (wr_addr[15:8]  == 8'h08);       // 0x0800 - 0x08FF

  logic [15:0] ra1, ra2;
  logic [31:0] reg_rd2;
  always_ff @(posedge clk) begin
    ra1 <= rd_addr; ra2 <= ra1;
    reg_rd2 <= (ra1[15:4] == 12'h004) ? ctrl_rd : reg_rd;
  end
  assign rd_data = (ra2[15:12] == 4'h1) ? sb_rd : (ra2[15:12] == 4'h2) ? af_rd :
                   (ra2[15:12] == 4'h0) ? reg_rd2 : 32'd0;

  // ------------------------------------------------------------------ stream
  // stage k output: dK, vK, uK, lK, rK (ready into the producer)
`define ISP_SKID(WID, sd, sv, su, sl, sr, md, mv, mu, ml, mr) \
  axis_skid #(.W((WID) + 2)) u_skid_``md (.clk, .rst_n(rst_dp_n), .s_data({su, sl, sd}), .s_valid(sv), .s_ready(sr), \
    .m_data({mu, ml, md}), .m_valid(mv), .m_ready(mr));

  logic [19:0] d_dpc, d_blc, d_aaf, d_awb, d_cnf, q_dpc, q_blc, q_aaf, q_awb, q_cnf;
  logic [59:0] d_cfa, d_ccm, q_cfa, q_ccm;
  logic [47:0] d_gam, d_csc, q_gam, q_csc;
  logic v_dpc, u_dpc, l_dpc, r_dpc, vq_dpc, uq_dpc, lq_dpc, rq_dpc;
  logic v_blc, u_blc, l_blc, r_blc, vq_blc, uq_blc, lq_blc, rq_blc;
  logic v_aaf, u_aaf, l_aaf, r_aaf, vq_aaf, uq_aaf, lq_aaf, rq_aaf;
  logic v_awb, u_awb, l_awb, r_awb, vq_awb, uq_awb, lq_awb, rq_awb;
  logic v_cnf, u_cnf, l_cnf, r_cnf, vq_cnf, uq_cnf, lq_cnf, rq_cnf;
  logic v_cfa, u_cfa, l_cfa, r_cfa, vq_cfa, uq_cfa, lq_cfa, rq_cfa;
  logic v_ccm, u_ccm, l_ccm, r_ccm, vq_ccm, uq_ccm, lq_ccm, rq_ccm;
  logic v_gam, u_gam, l_gam, r_gam, vq_gam, uq_gam, lq_gam, rq_gam;
  logic v_csc, u_csc, l_csc, r_csc, vq_csc, uq_csc, lq_csc, rq_csc;

  isp_dpc #(.MAXW(MAXW), .SB(SB)) i_dpc (
    .clk, .rst_n(rst_dp_n), .cfg_width(width), .cfg_height(height), .thres(dpc_thres), .mode_mean(dpc_mean),
    .s_tdata, .s_tvalid(s_tvalid && !gate), .s_tuser, .s_tlast, .s_tready(dpc_ready),
    .m_tdata(d_dpc), .m_tvalid(v_dpc), .m_tuser(u_dpc), .m_tlast(l_dpc), .m_tready(r_dpc));
  `ISP_SKID(20, d_dpc, v_dpc, u_dpc, l_dpc, r_dpc, q_dpc, vq_dpc, uq_dpc, lq_dpc, rq_dpc)

  isp_blc i_blc (
    .clk, .rst_n(rst_dp_n), .cfa, .bl({bl_b, bl_gb, bl_gr, bl_r}), .alpha(bl_alpha), .beta(bl_beta),
    .s_tdata(q_dpc), .s_tvalid(vq_dpc), .s_tuser(uq_dpc), .s_tlast(lq_dpc), .s_tready(rq_dpc),
    .m_tdata(d_blc), .m_tvalid(v_blc), .m_tuser(u_blc), .m_tlast(l_blc), .m_tready(r_blc));
  `ISP_SKID(20, d_blc, v_blc, u_blc, l_blc, r_blc, q_blc, vq_blc, uq_blc, lq_blc, rq_blc)

  stats_bayer #(.MAXW(MAXW), .SB(SB)) u_sb (
    .clk, .rst_n(rst_dp_n), .cfg_height(height), .cfa, .x0(st_x0), .y0(st_y0), .zw(st_zw), .zh(st_zh),
    .awb_sat, .awb_dark, .tdata(d_blc), .fire(v_blc && r_blc), .tuser(u_blc), .tlast(l_blc),
    .rd_addr(rd_addr[11:0]), .rd_data(sb_rd));

  isp_aaf #(.MAXW(MAXW), .SB(SB)) i_aaf (
    .clk, .rst_n(rst_dp_n), .cfg_width(width), .cfg_height(height),
    .s_tdata(q_blc), .s_tvalid(vq_blc), .s_tuser(uq_blc), .s_tlast(lq_blc), .s_tready(rq_blc),
    .m_tdata(d_aaf), .m_tvalid(v_aaf), .m_tuser(u_aaf), .m_tlast(l_aaf), .m_tready(r_aaf));
  `ISP_SKID(20, d_aaf, v_aaf, u_aaf, l_aaf, r_aaf, q_aaf, vq_aaf, uq_aaf, lq_aaf, rq_aaf)

  isp_awb i_awb (
    .clk, .rst_n(rst_dp_n), .cfa, .gain({awb_b, awb_gb, awb_gr, awb_r}),
    .s_tdata(q_aaf), .s_tvalid(vq_aaf), .s_tuser(uq_aaf), .s_tlast(lq_aaf), .s_tready(rq_aaf),
    .m_tdata(d_awb), .m_tvalid(v_awb), .m_tuser(u_awb), .m_tlast(l_awb), .m_tready(r_awb));
  `ISP_SKID(20, d_awb, v_awb, u_awb, l_awb, r_awb, q_awb, vq_awb, uq_awb, lq_awb, rq_awb)

  isp_cnf #(.MAXW(MAXW), .SB(SB)) i_cnf (
    .clk, .rst_n(rst_dp_n), .cfg_width(width), .cfg_height(height), .cfa, .thres(cnf_thres),
    .gain_r(awb_r), .gain_b(awb_b),
    .s_tdata(q_awb), .s_tvalid(vq_awb), .s_tuser(uq_awb), .s_tlast(lq_awb), .s_tready(rq_awb),
    .m_tdata(d_cnf), .m_tvalid(v_cnf), .m_tuser(u_cnf), .m_tlast(l_cnf), .m_tready(r_cnf));
  `ISP_SKID(20, d_cnf, v_cnf, u_cnf, l_cnf, r_cnf, q_cnf, vq_cnf, uq_cnf, lq_cnf, rq_cnf)

  isp_cfa #(.MAXW(MAXW), .SB(SB)) i_cfa (
    .clk, .rst_n(rst_dp_n), .cfg_width(width), .cfg_height(height), .cfa,
    .s_tdata(q_cnf), .s_tvalid(vq_cnf), .s_tuser(uq_cnf), .s_tlast(lq_cnf), .s_tready(rq_cnf),
    .m_tdata(d_cfa), .m_tvalid(v_cfa), .m_tuser(u_cfa), .m_tlast(l_cfa), .m_tready(r_cfa));
  `ISP_SKID(60, d_cfa, v_cfa, u_cfa, l_cfa, r_cfa, q_cfa, vq_cfa, uq_cfa, lq_cfa, rq_cfa)

  isp_mat3 #(.IB(10), .OB(10)) i_ccm (
    .clk, .rst_n(rst_dp_n),
    .coef({ccm_m8, ccm_m7, ccm_m6, ccm_m5, ccm_m4, ccm_m3, ccm_m2, ccm_m1, ccm_m0}),
    .offs({ccm_o2, ccm_o1, ccm_o0}),
    .s_tdata(q_cfa), .s_tvalid(vq_cfa), .s_tuser(uq_cfa), .s_tlast(lq_cfa), .s_tready(rq_cfa),
    .m_tdata(d_ccm), .m_tvalid(v_ccm), .m_tuser(u_ccm), .m_tlast(l_ccm), .m_tready(r_ccm));
  `ISP_SKID(60, d_ccm, v_ccm, u_ccm, l_ccm, r_ccm, q_ccm, vq_ccm, uq_ccm, lq_ccm, rq_ccm)

  isp_gamma u_gamma (
    .clk, .rst_n(rst_dp_n), .lut_we(gamma_we), .lut_addr(wr_addr[9:0]), .lut_data(wr_data[7:0]),
    .s_tdata(q_ccm), .s_tvalid(vq_ccm), .s_tuser(uq_ccm), .s_tlast(lq_ccm), .s_tready(rq_ccm),
    .m_tdata(d_gam), .m_tvalid(v_gam), .m_tuser(u_gam), .m_tlast(l_gam), .m_tready(r_gam));
  `ISP_SKID(48, d_gam, v_gam, u_gam, l_gam, r_gam, q_gam, vq_gam, uq_gam, lq_gam, rq_gam)

  isp_mat3 #(.IB(8), .OB(8)) i_csc (
    .clk, .rst_n(rst_dp_n),
    .coef({csc_m8, csc_m7, csc_m6, csc_m5, csc_m4, csc_m3, csc_m2, csc_m1, csc_m0}),
    .offs({csc_o2, csc_o1, csc_o0}),
    .s_tdata(q_gam), .s_tvalid(vq_gam), .s_tuser(uq_gam), .s_tlast(lq_gam), .s_tready(rq_gam),
    .m_tdata(d_csc), .m_tvalid(v_csc), .m_tuser(u_csc), .m_tlast(l_csc), .m_tready(r_csc));
  `ISP_SKID(48, d_csc, v_csc, u_csc, l_csc, r_csc, q_csc, vq_csc, uq_csc, lq_csc, rq_csc)

  isp_yuv #(.PPC(PPC), .MAXW(MAXW), .SB(SB)) u_yuv (
    .clk, .rst_n(rst_dp_n), .cfg_width(width), .cfg_height(height),
    .nlm_dshift, .nlm_lut_we(nlm_we), .nlm_lut_addr(wr_addr[7:0]), .nlm_lut_data(wr_data[7:0]),
    .bnf_dw(bnf_dw_v), .bnf_rw({bnf_rw3, bnf_rw2, bnf_rw1, bnf_rw0}), .bnf_rthres({bnf_t2, bnf_t1, bnf_t0}),
    .ee_filt(ee_f_v), .ee_gain({ee_g1, ee_g0}), .ee_thr({ee_t1, ee_t0}), .ee_emclip({ee_chi, ee_clo}),
    .fcs_e0, .fcs_e1, .fcs_gain, .hsc_sin, .hsc_cos, .hsc_sat, .bright, .contrast,
    .st_x0, .st_y0, .st_zw, .st_zh, .af_coring, .af_rd_addr(rd_addr[11:0]), .af_rd_data(af_rd),
    .s_tdata(q_csc), .s_tvalid(vq_csc), .s_tuser(uq_csc), .s_tlast(lq_csc), .s_tready(rq_csc),
    .m_tdata, .m_tvalid, .m_tuser, .m_tlast, .m_tready);
`undef ISP_SKID
endmodule
