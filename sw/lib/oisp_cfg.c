// SPDX-License-Identifier: MIT
/*
 * ISP configuration -> register words.  Mirror of hwmodel/config.py and
 * hwmodel/regmap.py (field_values, reg_words); tests/test_fw.py checks that
 * both produce the same words for random configurations.
 */
#include <ctype.h>
#include <math.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>

#define OISP_DEFINE_TABLES
#include "oisp.h"

static int fail(char *err, size_t len, const char *fmt, ...)
{
	va_list ap;

	if (err && len) {
		va_start(ap, fmt);
		vsnprintf(err, len, fmt, ap);
		va_end(ap);
	}
	return -1;
}

#define P(n, t, cnt, rows) { #n, t, offsetof(struct oisp_cfg, n), cnt, rows }
const struct oisp_param_desc oisp_params[] = {
	P(width, OISP_T_INT, 1, 1), P(height, OISP_T_INT, 1, 1),
	P(bayer_pattern, OISP_T_STR, 1, 1),
	P(dpc_thres, OISP_T_INT, 1, 1), P(dpc_mode, OISP_T_STR, 1, 1),
	P(bl, OISP_T_INT, 4, 1), P(bl_alpha, OISP_T_INT, 1, 1), P(bl_beta, OISP_T_INT, 1, 1),
	P(awb_gain, OISP_T_INT, 4, 1), P(cnf_thres, OISP_T_INT, 1, 1),
	P(ccm, OISP_T_INT, 12, 3), P(gamma, OISP_T_DOUBLE, 1, 1), P(csc, OISP_T_INT, 12, 3),
	P(nlm_h, OISP_T_INT, 1, 1), P(nlm_search_r, OISP_T_INT, 1, 1), P(nlm_dshift, OISP_T_INT, 1, 1),
	P(bnf_dw, OISP_T_INT, 25, 5), P(bnf_rw, OISP_T_INT, 4, 1), P(bnf_rthres, OISP_T_INT, 3, 1),
	P(edge_filter, OISP_T_INT, 15, 3), P(ee_gain, OISP_T_INT, 2, 1), P(ee_thres, OISP_T_INT, 2, 1),
	P(ee_emclip, OISP_T_INT, 2, 1), P(fcs_edge, OISP_T_INT, 2, 1), P(fcs_gain, OISP_T_INT, 1, 1),
	P(hue, OISP_T_INT, 1, 1), P(saturation, OISP_T_INT, 1, 1),
	P(brightness, OISP_T_INT, 1, 1), P(contrast, OISP_T_INT, 1, 1),
	P(stats_x0, OISP_T_INT, 1, 1), P(stats_y0, OISP_T_INT, 1, 1),
	P(stats_zw, OISP_T_INT, 1, 1), P(stats_zh, OISP_T_INT, 1, 1),
	P(awb_sat, OISP_T_INT, 1, 1), P(awb_dark, OISP_T_INT, 1, 1), P(af_coring, OISP_T_INT, 1, 1),
	{ NULL, OISP_T_INT, 0, 0, 0 }
};
#undef P

const struct oisp_param_desc *oisp_param_find(const char *name)
{
	const struct oisp_param_desc *d;

	for (d = oisp_params; d->name; d++)
		if (!strcmp(d->name, name))
			return d;
	return NULL;
}

void oisp_cfg_default(struct oisp_cfg *c)
{
	static const int csc[3][4] = { { 263, 516, 100, 16384 }, { -152, -298, 450, 131072 },
				       { 450, -377, -73, 131072 } };
	static const int dw[5][5] = { { 8, 12, 32, 12, 8 }, { 12, 64, 128, 64, 12 },
				      { 32, 128, 1024, 128, 32 }, { 12, 64, 128, 64, 12 },
				      { 8, 12, 32, 12, 8 } };
	static const int ef[3][5] = { { -1, 0, -1, 0, -1 }, { -1, 0, 8, 0, -1 }, { -1, 0, -1, 0, -1 } };
	int i;

	memset(c, 0, sizeof(*c));
	c->width = 1920;
	c->height = 1080;
	strcpy(c->bayer_pattern, "rggb");
	c->dpc_thres = 30;
	strcpy(c->dpc_mode, "gradient");
	c->awb_gain[0] = 1536;
	c->awb_gain[1] = c->awb_gain[2] = 1024;
	c->awb_gain[3] = 1126;
	for (i = 0; i < 3; i++)
		c->ccm[i][i] = 1024;
	c->gamma = 0.45;
	memcpy(c->csc, csc, sizeof(csc));
	c->nlm_h = 15;
	c->nlm_search_r = 2;
	c->nlm_dshift = 3;
	memcpy(c->bnf_dw, dw, sizeof(dw));
	c->bnf_rw[0] = 0; c->bnf_rw[1] = 8; c->bnf_rw[2] = 16; c->bnf_rw[3] = 32;
	c->bnf_rthres[0] = 128; c->bnf_rthres[1] = 32; c->bnf_rthres[2] = 8;
	memcpy(c->edge_filter, ef, sizeof(ef));
	c->ee_gain[0] = 32; c->ee_gain[1] = 128;
	c->ee_thres[0] = 32; c->ee_thres[1] = 64;
	c->ee_emclip[0] = -64; c->ee_emclip[1] = 64;
	c->fcs_edge[0] = 32; c->fcs_edge[1] = 64;
	c->fcs_gain = 256;
	c->saturation = 256;
	c->awb_sat = 1020;
	c->awb_dark = 16;
	c->af_coring = 4;
}

int oisp_zone_grid(int width, int height, int x0, int y0, int zw, int zh,
		   struct oisp_grid *g, char *err, size_t errlen)
{
	int autog = !zw && !zh;

	if (!zw)
		zw = ((width - x0) / OISP_NZ) & ~1;
	if (!zh)
		zh = ((height - y0) / OISP_NZ) & ~1;
	if (autog && !x0 && !y0) {
		x0 = ((width - OISP_NZ * zw) / 2) & ~1;
		y0 = ((height - OISP_NZ * zh) / 2) & ~1;
	}
	if ((x0 | y0 | zw | zh) & 1 || zw <= 0 || zh <= 0 || x0 < 0 || y0 < 0)
		return fail(err, errlen, "statistics grid must be even and non-empty");
	if (x0 + OISP_NZ * zw > width || y0 + OISP_NZ * zh > height)
		return fail(err, errlen, "statistics grid exceeds the frame");
	g->x0 = x0; g->y0 = y0; g->zw = zw; g->zh = zh;
	return 0;
}

/* channel id (0 R, 1 Gr, 2 Gb, 3 B) of pixel (y odd, x odd) at bits 2*(2y+x) */
static int cfa_code(const char *pat, int *code)
{
	static const struct { const char *n; int t[4]; } pats[] = {
		{ "rggb", { 0, 1, 2, 3 } }, { "bggr", { 3, 2, 1, 0 } },
		{ "grbg", { 1, 0, 3, 2 } }, { "gbrg", { 2, 3, 0, 1 } },
	};
	unsigned int i;
	int k;

	for (i = 0; i < sizeof(pats) / sizeof(pats[0]); i++) {
		if (strcmp(pat, pats[i].n))
			continue;
		*code = 0;
		for (k = 0; k < 4; k++)
			*code |= pats[i].t[k] << (2 * k);
		return 0;
	}
	return -1;
}

int oisp_cfg_fields(const struct oisp_cfg *c, int32_t f[OISP_NFIELDS], char *err, size_t errlen)
{
	struct oisp_grid g;
	int k, cfa, hue;

	if (cfa_code(c->bayer_pattern, &cfa))
		return fail(err, errlen, "unknown bayer_pattern \"%s\"", c->bayer_pattern);
	if (strcmp(c->dpc_mode, "gradient") && strcmp(c->dpc_mode, "mean"))
		return fail(err, errlen, "unknown dpc_mode \"%s\"", c->dpc_mode);
	f[OISP_F_WIDTH] = c->width;
	f[OISP_F_HEIGHT] = c->height;
	f[OISP_F_CFA] = cfa;
	f[OISP_F_DPC_THRES] = c->dpc_thres;
	f[OISP_F_DPC_MEAN] = !strcmp(c->dpc_mode, "mean");
	f[OISP_F_BL_R] = c->bl[0]; f[OISP_F_BL_GR] = c->bl[1];
	f[OISP_F_BL_GB] = c->bl[2]; f[OISP_F_BL_B] = c->bl[3];
	f[OISP_F_BL_ALPHA] = c->bl_alpha;
	f[OISP_F_BL_BETA] = c->bl_beta;
	f[OISP_F_AWB_R] = c->awb_gain[0]; f[OISP_F_AWB_GR] = c->awb_gain[1];
	f[OISP_F_AWB_GB] = c->awb_gain[2]; f[OISP_F_AWB_B] = c->awb_gain[3];
	f[OISP_F_CNF_THRES] = c->cnf_thres;
	for (k = 0; k < 9; k++) {
		f[OISP_F_CCM_M0 + k] = c->ccm[k / 3][k % 3];
		f[OISP_F_CSC_M0 + k] = c->csc[k / 3][k % 3];
	}
	for (k = 0; k < 3; k++) {
		f[OISP_F_CCM_O0 + k] = c->ccm[k][3];
		f[OISP_F_CSC_O0 + k] = c->csc[k][3];
	}
	f[OISP_F_NLM_DSHIFT] = c->nlm_dshift;
	for (k = 0; k < 25; k++)
		f[OISP_F_BNF_DW0 + k] = c->bnf_dw[k / 5][k % 5];
	for (k = 0; k < 4; k++)
		f[OISP_F_BNF_RW0 + k] = c->bnf_rw[k];
	for (k = 0; k < 3; k++)
		f[OISP_F_BNF_T0 + k] = c->bnf_rthres[k];
	for (k = 0; k < 15; k++)
		f[OISP_F_EE_F0 + k] = c->edge_filter[k / 5][k % 5];
	f[OISP_F_EE_G0] = c->ee_gain[0]; f[OISP_F_EE_G1] = c->ee_gain[1];
	f[OISP_F_EE_T0] = c->ee_thres[0]; f[OISP_F_EE_T1] = c->ee_thres[1];
	f[OISP_F_EE_CLO] = c->ee_emclip[0]; f[OISP_F_EE_CHI] = c->ee_emclip[1];
	f[OISP_F_FCS_E0] = c->fcs_edge[0]; f[OISP_F_FCS_E1] = c->fcs_edge[1];
	f[OISP_F_FCS_GAIN] = c->fcs_gain;
	hue = ((c->hue % 360) + 360) % 360;
	f[OISP_F_HSC_SIN] = oisp_hsc_sin[hue];
	f[OISP_F_HSC_COS] = oisp_hsc_cos[hue];
	f[OISP_F_HSC_SAT] = c->saturation;
	f[OISP_F_BRIGHT] = c->brightness;
	f[OISP_F_CONTRAST] = c->contrast;
	if (oisp_zone_grid(c->width, c->height, c->stats_x0, c->stats_y0, c->stats_zw, c->stats_zh,
			   &g, err, errlen))
		return -1;
	f[OISP_F_ST_X0] = g.x0; f[OISP_F_ST_Y0] = g.y0;
	f[OISP_F_ST_ZW] = g.zw; f[OISP_F_ST_ZH] = g.zh;
	f[OISP_F_AWB_SAT] = c->awb_sat;
	f[OISP_F_AWB_DARK] = c->awb_dark;
	f[OISP_F_AF_CORING] = c->af_coring;
	return 0;
}

int oisp_pack_words(const int32_t f[OISP_NFIELDS], uint32_t w[OISP_NREG], char *err, size_t errlen)
{
	int i;

	memset(w, 0, sizeof(uint32_t) * OISP_NREG);
	for (i = 0; i < OISP_NFIELDS; i++) {
		const struct oisp_field_desc *d = &oisp_fields[i];
		int64_t lo = d->is_signed ? -(1LL << (d->width - 1)) : 0;
		int64_t hi = d->is_signed ? (1LL << (d->width - 1)) - 1 : (1LL << d->width) - 1;
		uint32_t mask = d->width == 32 ? 0xffffffffu : (1u << d->width) - 1;

		if (f[i] < lo || f[i] > hi)
			return fail(err, errlen, "%s = %d does not fit %c%d", d->name, f[i],
				    d->is_signed ? 's' : 'u', d->width);
		w[d->word] |= ((uint32_t)f[i] & mask) << d->lsb;
	}
	return 0;
}

int oisp_cfg_words(const struct oisp_cfg *c, uint32_t w[OISP_NREG], char *err, size_t errlen)
{
	int32_t f[OISP_NFIELDS];

	if (oisp_cfg_fields(c, f, err, errlen))
		return -1;
	return oisp_pack_words(f, w, err, errlen);
}

void oisp_gamma_lut(double gamma, uint8_t lut[OISP_GAMMA_SIZE])
{
	int i;

	for (i = 0; i < OISP_GAMMA_SIZE; i++)
		lut[i] = (uint8_t)floor(255.0 * pow(i / 1023.0, gamma) + 0.5);
}

void oisp_nlm_lut(int h, int dshift, uint8_t lut[OISP_NLM_LUT_SIZE])
{
	int i;

	for (i = 0; i < OISP_NLM_LUT_SIZE; i++) {
		double mad = (double)(i << dshift) / 9.0;

		lut[i] = (uint8_t)floor(255.0 * exp(-(mad * mad) / (double)(h * h)) + 0.5);
	}
}

void oisp_cfg_tables(struct oisp_cfg *c)
{
	if (!c->gamma_lut_set)
		oisp_gamma_lut(c->gamma, c->gamma_lut);
	if (!c->nlm_lut_set)
		oisp_nlm_lut(c->nlm_h, c->nlm_dshift, c->nlm_lut);
}

/* ------------------------------------------------------------------ text access */
static int parse_numbers(const char *s, double *out, int max)
{
	int n = 0;
	char *end;

	while (*s) {
		if (isspace((unsigned char)*s) || *s == ',' || *s == ';' || *s == '[' || *s == ']') {
			s++;
			continue;
		}
		if (n == max)
			return -1;
		out[n] = strtod(s, &end);
		if (end == s)
			return -1;
		n++;
		s = end;
	}
	return n;
}

int oisp_cfg_set(struct oisp_cfg *c, const char *name, const char *values, char *err, size_t errlen)
{
	const struct oisp_param_desc *d;
	double v[OISP_GAMMA_SIZE];
	char *base = (char *)c;
	int n, i;

	if (!strcmp(name, "gamma_lut") || !strcmp(name, "nlm_lut")) {
		int size = name[0] == 'g' ? OISP_GAMMA_SIZE : OISP_NLM_LUT_SIZE;
		uint8_t *lut = name[0] == 'g' ? c->gamma_lut : c->nlm_lut;

		n = parse_numbers(values, v, OISP_GAMMA_SIZE);
		if (n != size)
			return fail(err, errlen, "%s needs %d values", name, size);
		for (i = 0; i < size; i++) {
			if (v[i] < 0 || v[i] > 255)
				return fail(err, errlen, "%s[%d] out of range", name, i);
			lut[i] = (uint8_t)v[i];
		}
		if (name[0] == 'g')
			c->gamma_lut_set = 1;
		else
			c->nlm_lut_set = 1;
		return 0;
	}
	d = oisp_param_find(name);
	if (!d)
		return fail(err, errlen, "unknown parameter \"%s\"", name);
	if (d->type == OISP_T_STR) {
		char *dst = base + d->offset;
		size_t cap = !strcmp(name, "dpc_mode") ? sizeof(c->dpc_mode) : sizeof(c->bayer_pattern);

		while (isspace((unsigned char)*values) || *values == '"')
			values++;
		for (i = 0; i < (int)cap - 1 && values[i] && values[i] != '"' && !isspace((unsigned char)values[i]); i++)
			dst[i] = values[i];
		dst[i] = 0;
		return 0;
	}
	n = parse_numbers(values, v, d->count);
	if (n != d->count)
		return fail(err, errlen, "%s needs %d values", name, d->count);
	if (d->type == OISP_T_DOUBLE) {
		*(double *)(base + d->offset) = v[0];
	} else {
		for (i = 0; i < n; i++) {
			if (v[i] != floor(v[i]))
				return fail(err, errlen, "%s: integer expected", name);
			((int *)(base + d->offset))[i] = (int)v[i];
		}
	}
	if (!strcmp(name, "gamma"))
		c->gamma_lut_set = 0;
	if (!strcmp(name, "nlm_h") || !strcmp(name, "nlm_dshift"))
		c->nlm_lut_set = 0;
	return 0;
}

int oisp_cfg_get(const struct oisp_cfg *c, const char *name, char *buf, size_t len)
{
	const struct oisp_param_desc *d = oisp_param_find(name);
	const char *base = (const char *)c;
	size_t pos = 0;
	int i;

	if (!d)
		return -1;
	buf[0] = 0;
	if (d->type == OISP_T_STR)
		return snprintf(buf, len, "%s", base + d->offset) < 0 ? -1 : 0;
	if (d->type == OISP_T_DOUBLE)
		return snprintf(buf, len, "%.17g", *(const double *)(base + d->offset)) < 0 ? -1 : 0;
	for (i = 0; i < d->count && pos < len; i++)
		pos += snprintf(buf + pos, len - pos, "%s%d", i ? " " : "",
				((const int *)(base + d->offset))[i]);
	return 0;
}

void oisp_cfg_write_json(const struct oisp_cfg *c, FILE *f)
{
	const struct oisp_param_desc *d;
	const char *base = (const char *)c;
	int i, first = 1;

	fputs("{", f);
	for (d = oisp_params; d->name; d++) {
		fprintf(f, "%s\"%s\": ", first ? "" : ", ", d->name);
		first = 0;
		if (d->type == OISP_T_STR) {
			fprintf(f, "\"%s\"", base + d->offset);
		} else if (d->type == OISP_T_DOUBLE) {
			fprintf(f, "%.17g", *(const double *)(base + d->offset));
		} else if (d->count == 1) {
			fprintf(f, "%d", *(const int *)(base + d->offset));
		} else {
			int cols = d->count / d->rows;

			fputs("[", f);
			for (i = 0; i < d->count; i++) {
				if (d->rows > 1 && i % cols == 0)
					fputs(i ? "], [" : "[", f);
				else if (i)
					fputs(", ", f);
				fprintf(f, "%d", ((const int *)(base + d->offset))[i]);
			}
			fputs(d->rows > 1 ? "]]" : "]", f);
		}
	}
	fputs("}", f);
}
