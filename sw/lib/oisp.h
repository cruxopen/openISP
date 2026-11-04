/* SPDX-License-Identifier: MIT */
/*
 * libopenisp - firmware library of the openISP hardware ISP.
 *
 *   oisp_cfg     ISP configuration (mirror of hwmodel.config.HwConfig) and its
 *                translation to register words (mirror of hwmodel.regmap)
 *   oisp_json    small JSON reader / writer (tuning files, live protocol)
 *   oisp_tuning  tuning file -> configuration + 3A parameters
 *   oisp_aaa     AE / AWB / AF control loops (mirror of hwmodel/aaa.py)
 *   oisp_hal     hardware access: kernel driver, /dev/mem, or simulation
 *   oisp_sensor  sensor exposure / gain through V4L2 controls
 */
#ifndef OISP_H
#define OISP_H

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

#include "openisp_regs.h"
#include "openisp_uapi.h"

#define OISP_NZ 16

/* ------------------------------------------------------------------ config */
struct oisp_cfg {
	int width, height;
	char bayer_pattern[8];
	int dpc_thres;
	char dpc_mode[16];		/* "gradient" | "mean" */
	int bl[4], bl_alpha, bl_beta;
	int awb_gain[4];
	int cnf_thres;
	int ccm[3][4];
	double gamma;
	int csc[3][4];
	int nlm_h, nlm_search_r, nlm_dshift;
	int bnf_dw[5][5], bnf_rw[4], bnf_rthres[3];
	int edge_filter[3][5];
	int ee_gain[2], ee_thres[2], ee_emclip[2];
	int fcs_edge[2], fcs_gain;
	int hue, saturation, brightness, contrast;
	int stats_x0, stats_y0, stats_zw, stats_zh;
	int awb_sat, awb_dark, af_coring;
	/* tables; computed from gamma / nlm_h when not given explicitly */
	int gamma_lut_set, nlm_lut_set;
	uint8_t gamma_lut[OISP_GAMMA_SIZE];
	uint8_t nlm_lut[OISP_NLM_LUT_SIZE];
};

enum oisp_type { OISP_T_INT, OISP_T_DOUBLE, OISP_T_STR };
struct oisp_param_desc {
	const char *name;
	enum oisp_type type;
	size_t offset;
	int count;			/* number of ints (matrices row major) */
	int rows;			/* > 1: matrix with count / rows columns */
};
extern const struct oisp_param_desc oisp_params[];
const struct oisp_param_desc *oisp_param_find(const char *name);

struct oisp_grid { int x0, y0, zw, zh; };

void oisp_cfg_default(struct oisp_cfg *c);
int oisp_zone_grid(int width, int height, int x0, int y0, int zw, int zh,
		   struct oisp_grid *g, char *err, size_t errlen);
/* configuration -> field values (hwmodel.regmap.field_values) */
int oisp_cfg_fields(const struct oisp_cfg *c, int32_t f[OISP_NFIELDS], char *err, size_t errlen);
/* field values -> register words, range checked (hwmodel.regmap.reg_words) */
int oisp_pack_words(const int32_t f[OISP_NFIELDS], uint32_t w[OISP_NREG], char *err, size_t errlen);
int oisp_cfg_words(const struct oisp_cfg *c, uint32_t w[OISP_NREG], char *err, size_t errlen);
void oisp_gamma_lut(double gamma, uint8_t lut[OISP_GAMMA_SIZE]);
void oisp_nlm_lut(int h, int dshift, uint8_t lut[OISP_NLM_LUT_SIZE]);
void oisp_cfg_tables(struct oisp_cfg *c);	/* fill the tables that were not set */

/* ------------------------------------------------------------------ json */
enum oisp_jtype { OJ_NULL, OJ_BOOL, OJ_NUM, OJ_STR, OJ_ARR, OJ_OBJ };
struct oisp_json {
	enum oisp_jtype type;
	double num;
	char *str;			/* OJ_STR value; key of an object member */
	char *key;
	struct oisp_json *child, *next;
	int n;				/* number of children */
};
struct oisp_json *oisp_json_parse(const char *text, char *err, size_t errlen);
struct oisp_json *oisp_json_load(const char *path, char *err, size_t errlen);
void oisp_json_free(struct oisp_json *j);
struct oisp_json *oisp_json_get(const struct oisp_json *obj, const char *key);
struct oisp_json *oisp_json_at(const struct oisp_json *arr, int i);
/* flatten nested numeric arrays into out[0..max), returns the count or -1 */
int oisp_json_numbers(const struct oisp_json *j, double *out, int max);

/* ------------------------------------------------------------------ 3A */
#define OISP_MAX_ILLUM 8
#define OISP_MAX_GAIN 64
struct oisp_illum {
	char name[16];
	double cct;
	double gain_r, gain_b;		/* Q10 */
	int ccm[3][4];
};
struct oisp_awb_param {
	int enable, min_quads;
	double speed, locus_dist;
	int gain_min, gain_max;
	int n_illum;
	struct oisp_illum ill[OISP_MAX_ILLUM];
};
struct oisp_awb_state {
	int valid, k;
	double lr, lb, f;
	/* outputs */
	int gain_r, gain_b, ccm_valid, ccm[3][4];
	double cct;
};
struct oisp_ae_param {
	int enable;
	double target, damping, ratio_max;
	int hl_bin;
	double hl_frac, hl_scale;
	int lines_min, lines_max, flicker_lines;
	int gain_1x, gain_code_min, gain_code_max;
	/* sensors whose gain control is an index: gain of code i (n_gain > 0
	 * replaces code = gain x gain_1x; codes are gain_code_min..max) */
	int n_gain;
	double gain_table[OISP_MAX_GAIN];
	double dgain_max;
	int delay;			/* frames until a new exposure is visible */
};
struct oisp_ae_out {
	int lines, gain_code;
	double again, dgain, exposure, mean;
};
struct oisp_af_param {
	int enable;
	int roi[4];			/* zone rows [0]..[1), cols [2]..[3) */
	int pos_min, pos_max, step;
};
struct oisp_af_state {
	int phase;			/* 0 idle, 1 sweeping, 2 locked */
	int pos, best_pos;
	uint64_t best, locked_score, score;
};

void oisp_ae_default(struct oisp_ae_param *p);
void oisp_awb_default(struct oisp_awb_param *p);
void oisp_af_default(struct oisp_af_param *p);
double oisp_ae_measure(const struct openisp_stats *st, const struct oisp_grid *g);
double oisp_ae_target(const struct oisp_ae_param *p, const struct openisp_stats *st,
		      const struct oisp_grid *g, double e_used, double dg_used, double *mean);
void oisp_ae_split(const struct oisp_ae_param *p, double e, struct oisp_ae_out *o);
int oisp_awb_step(const struct oisp_awb_param *p, struct oisp_awb_state *s,
		  const struct openisp_stats *st);
void oisp_wb_registers(int gain_r, int gain_b, double dgain, int out[4]);
uint64_t oisp_af_score(const struct oisp_af_param *p, const struct openisp_stats *st);
/* contrast AF sweep; returns the next lens position to apply or -1 */
int oisp_af_step(const struct oisp_af_param *p, struct oisp_af_state *s, uint64_t score);

/* ------------------------------------------------------------------ tuning */
struct oisp_tuning {
	struct oisp_cfg cfg;
	struct oisp_ae_param ae;
	struct oisp_awb_param awb;
	struct oisp_af_param af;
	char sensor[32];		/* V4L2 sub-device name of the sensor (substring) */
	char exposure_ctrl[32];		/* control names when not the standard CIDs */
	char gain_ctrl[32];
};
void oisp_tuning_default(struct oisp_tuning *t);
int oisp_tuning_load(struct oisp_tuning *t, const char *path, char *err, size_t errlen);
int oisp_tuning_apply_json(struct oisp_tuning *t, const struct oisp_json *root, char *err, size_t errlen);
/* set one configuration parameter from text ("ccm 1024 0 ...", "gamma 0.45") */
int oisp_cfg_set(struct oisp_cfg *c, const char *name, const char *values, char *err, size_t errlen);
int oisp_cfg_get(const struct oisp_cfg *c, const char *name, char *buf, size_t len);
void oisp_cfg_write_json(const struct oisp_cfg *c, FILE *f);

/* ------------------------------------------------------------------ hal */
struct oisp_hal;
struct oisp_hal_ops {
	int (*write)(struct oisp_hal *h, const struct openisp_reg_write *w, int n, int commit);
	int (*read)(struct oisp_hal *h, uint32_t addr, uint32_t *v);
	int (*stats)(struct oisp_hal *h, struct openisp_stats *st, uint32_t after, int timeout_ms);
	int (*info)(struct oisp_hal *h, struct openisp_info *info);
	void (*close)(struct oisp_hal *h);
};
struct oisp_hal {
	const struct oisp_hal_ops *ops;
	int fd;
	volatile uint32_t *mmio;
	size_t mmio_len;
	FILE *log;			/* sim: write log */
	const char *stats_dir;		/* sim: stats_<n>.bin files */
	uint32_t sim_frame;
	uint32_t sim_width, sim_height;
};
/* "/dev/v4l-subdevN" | "mem:0xA0000000" | "sim:<log file>:<stats dir>:<w>x<h>" | NULL (auto) */
struct oisp_hal *oisp_hal_open(const char *spec, char *err, size_t errlen);
void oisp_hal_close(struct oisp_hal *h);
/* configuration words + tables (tables only when with_tables) */
int oisp_hal_apply(struct oisp_hal *h, const struct oisp_cfg *c, int with_tables, char *err, size_t errlen);

/* ------------------------------------------------------------------ sensor */
struct oisp_sensor {
	int fd;
	int has_exposure, has_gain, has_focus;
	int exp_min, exp_max, gain_min, gain_max, focus_min, focus_max;
	uint32_t exp_cid, gain_cid;
	char exp_name[32], gain_name[32];
};
/* exposure_ctrl / gain_ctrl: V4L2 control names, or NULL/"" for the standard
 * CIDs (EXPOSURE; ANALOGUE_GAIN, else GAIN) with a fallback by name */
int oisp_sensor_open(struct oisp_sensor *s, const char *name_or_path, const char *exposure_ctrl,
		     const char *gain_ctrl, char *err, size_t errlen);
int oisp_sensor_set(struct oisp_sensor *s, int lines, int gain_code);
int oisp_sensor_focus(struct oisp_sensor *s, int pos);
void oisp_sensor_close(struct oisp_sensor *s);

#endif /* OISP_H */
