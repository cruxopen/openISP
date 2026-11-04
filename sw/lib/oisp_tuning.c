// SPDX-License-Identifier: MIT
/*
 * Tuning file (JSON, written by the PC tuning tool tuning/isptune):
 *
 *   { "version": 1,
 *     "sensor": { "name": "ar1335", "exposure_ctrl": "coarse_integration_time", "gain_ctrl": "gain" },
 *     "isp":    { <HwConfig fields>, "gamma_lut": [1024], "nlm_lut": [256] },
 *     "ae":     { "enable": true, "target": 184, ..., "gain_table": [1, 2, 4, ...] },
 *     "awb":    { "enable": true, ..., "illuminants": [
 *                   { "name": "A", "cct": 2856, "gains": [r, b], "ccm": [[..4], [..4], [..4]] } ] },
 *     "af":     { "enable": false, "roi": [4, 12, 4, 12], ... } }
 *
 * Every section and key is optional; unknown keys are errors (typos).
 */
#include <stdarg.h>
#include <string.h>

#include "oisp.h"

static int tfail(char *err, size_t len, const char *fmt, ...)
{
	va_list ap;

	va_start(ap, fmt);
	vsnprintf(err, len, fmt, ap);
	va_end(ap);
	return -1;
}

void oisp_tuning_default(struct oisp_tuning *t)
{
	memset(t, 0, sizeof(*t));
	oisp_cfg_default(&t->cfg);
	oisp_ae_default(&t->ae);
	oisp_awb_default(&t->awb);
	oisp_af_default(&t->af);
}

static int set_cfg(struct oisp_cfg *c, const struct oisp_json *j, char *err, size_t errlen)
{
	double v[OISP_GAMMA_SIZE];
	const struct oisp_json *m;
	int n, i, pass;

	/* pass 0: parameters, pass 1: explicit tables (override gamma / nlm_h) */
	for (pass = 0; pass < 2; pass++)
	for (m = j->child; m; m = m->next) {
		const struct oisp_param_desc *d = oisp_param_find(m->key);
		char *base = (char *)c;
		int is_table = !strcmp(m->key, "gamma_lut") || !strcmp(m->key, "nlm_lut");

		if (is_table != pass)
			continue;
		if (is_table) {
			int size = m->key[0] == 'g' ? OISP_GAMMA_SIZE : OISP_NLM_LUT_SIZE;
			uint8_t *lut = m->key[0] == 'g' ? c->gamma_lut : c->nlm_lut;

			if (m->type == OJ_NULL)
				continue;
			n = oisp_json_numbers(m, v, OISP_GAMMA_SIZE);
			if (n != size)
				return tfail(err, errlen, "isp.%s: %d values expected", m->key, size);
			for (i = 0; i < size; i++) {
				if (v[i] < 0 || v[i] > 255)
					return tfail(err, errlen, "isp.%s[%d] out of range", m->key, i);
				lut[i] = (uint8_t)v[i];
			}
			*(m->key[0] == 'g' ? &c->gamma_lut_set : &c->nlm_lut_set) = 1;
			continue;
		}
		if (!d)
			return tfail(err, errlen, "isp: unknown parameter \"%s\"", m->key);
		if (d->type == OISP_T_STR) {
			if (m->type != OJ_STR)
				return tfail(err, errlen, "isp.%s: string expected", m->key);
			if (oisp_cfg_set(c, m->key, m->str, err, errlen))
				return -1;
			continue;
		}
		n = oisp_json_numbers(m, v, d->count);
		if (n != d->count)
			return tfail(err, errlen, "isp.%s: %d numbers expected", m->key, d->count);
		if (d->type == OISP_T_DOUBLE) {
			*(double *)(base + d->offset) = v[0];
			c->gamma_lut_set = 0;
		} else {
			for (i = 0; i < n; i++)
				((int *)(base + d->offset))[i] = (int)v[i];
			if (!strcmp(m->key, "nlm_h") || !strcmp(m->key, "nlm_dshift"))
				c->nlm_lut_set = 0;
		}
	}
	return 0;
}

/* generic numeric members of a parameter struct */
struct num_desc {
	const char *name;
	int is_double;
	size_t offset;
	int count;
};

static int set_nums(void *obj, const struct num_desc *nd, const struct oisp_json *j,
		    const char *sec, const char *skip, char *err, size_t errlen)
{
	const struct oisp_json *m;
	double v[8];

	for (m = j->child; m; m = m->next) {
		const struct num_desc *d;
		int n, i;

		if (skip && !strcmp(m->key, skip))
			continue;
		for (d = nd; d->name && strcmp(d->name, m->key); d++)
			;
		if (!d->name)
			return tfail(err, errlen, "%s: unknown parameter \"%s\"", sec, m->key);
		n = oisp_json_numbers(m, v, 8);
		if (n != d->count)
			return tfail(err, errlen, "%s.%s: %d numbers expected", sec, m->key, d->count);
		for (i = 0; i < n; i++) {
			if (d->is_double)
				((double *)((char *)obj + d->offset))[i] = v[i];
			else
				((int *)((char *)obj + d->offset))[i] = (int)v[i];
		}
	}
	return 0;
}

#define NI(s, f, n) { #f, 0, offsetof(struct s, f), n }
#define ND(s, f, n) { #f, 1, offsetof(struct s, f), n }
static const struct num_desc ae_desc[] = {
	NI(oisp_ae_param, enable, 1), ND(oisp_ae_param, target, 1), ND(oisp_ae_param, damping, 1),
	ND(oisp_ae_param, ratio_max, 1), NI(oisp_ae_param, hl_bin, 1), ND(oisp_ae_param, hl_frac, 1),
	ND(oisp_ae_param, hl_scale, 1), NI(oisp_ae_param, lines_min, 1), NI(oisp_ae_param, lines_max, 1),
	NI(oisp_ae_param, flicker_lines, 1), NI(oisp_ae_param, gain_1x, 1),
	NI(oisp_ae_param, gain_code_min, 1), NI(oisp_ae_param, gain_code_max, 1),
	ND(oisp_ae_param, dgain_max, 1), NI(oisp_ae_param, delay, 1), { NULL, 0, 0, 0 }
};
static const struct num_desc awb_desc[] = {
	NI(oisp_awb_param, enable, 1), NI(oisp_awb_param, min_quads, 1), ND(oisp_awb_param, speed, 1),
	ND(oisp_awb_param, locus_dist, 1), NI(oisp_awb_param, gain_min, 1),
	NI(oisp_awb_param, gain_max, 1), { NULL, 0, 0, 0 }
};
static const struct num_desc af_desc[] = {
	NI(oisp_af_param, enable, 1), NI(oisp_af_param, roi, 4), NI(oisp_af_param, pos_min, 1),
	NI(oisp_af_param, pos_max, 1), NI(oisp_af_param, step, 1), { NULL, 0, 0, 0 }
};

static int set_gain_table(struct oisp_ae_param *p, const struct oisp_json *arr, char *err, size_t errlen)
{
	int n, i;

	if (arr->type != OJ_ARR)
		return tfail(err, errlen, "ae.gain_table: array expected");
	if (arr->n > OISP_MAX_GAIN)
		return tfail(err, errlen, "ae.gain_table: at most %d entries", OISP_MAX_GAIN);
	n = oisp_json_numbers(arr, p->gain_table, OISP_MAX_GAIN);
	if (n != arr->n)
		return tfail(err, errlen, "ae.gain_table: numbers expected");
	for (i = 0; i < n; i++)
		if (p->gain_table[i] <= 0 || (i && p->gain_table[i] < p->gain_table[i - 1]))
			return tfail(err, errlen, "ae.gain_table: positive and non-decreasing");
	p->n_gain = n;
	return 0;
}

static int set_sensor(struct oisp_tuning *t, const struct oisp_json *j, char *err, size_t errlen)
{
	const struct oisp_json *m;

	for (m = j->child; m; m = m->next) {
		char *dst = !strcmp(m->key, "name") ? t->sensor :
			    !strcmp(m->key, "exposure_ctrl") ? t->exposure_ctrl :
			    !strcmp(m->key, "gain_ctrl") ? t->gain_ctrl : NULL;

		if (!dst)
			return tfail(err, errlen, "sensor: unknown key \"%s\"", m->key);
		if (m->type != OJ_STR)
			return tfail(err, errlen, "sensor.%s: string expected", m->key);
		snprintf(dst, sizeof(t->sensor), "%s", m->str);
	}
	return 0;
}

static int set_illuminants(struct oisp_awb_param *p, const struct oisp_json *arr, char *err, size_t errlen)
{
	const struct oisp_json *e;
	double v[12];
	int k = 0, i;

	if (arr->type != OJ_ARR)
		return tfail(err, errlen, "awb.illuminants: array expected");
	if (arr->n > OISP_MAX_ILLUM)
		return tfail(err, errlen, "awb.illuminants: at most %d", OISP_MAX_ILLUM);
	for (e = arr->child; e; e = e->next, k++) {
		struct oisp_illum *il = &p->ill[k];
		const struct oisp_json *nm = oisp_json_get(e, "name");

		memset(il, 0, sizeof(*il));
		if (nm && nm->type == OJ_STR)
			snprintf(il->name, sizeof(il->name), "%s", nm->str);
		if (oisp_json_numbers(oisp_json_get(e, "cct"), v, 1) != 1 || v[0] <= 0)
			return tfail(err, errlen, "awb.illuminants[%d].cct missing", k);
		il->cct = v[0];
		if (oisp_json_numbers(oisp_json_get(e, "gains"), v, 2) != 2 || v[0] <= 0 || v[1] <= 0)
			return tfail(err, errlen, "awb.illuminants[%d].gains: [r, b] expected", k);
		il->gain_r = v[0];
		il->gain_b = v[1];
		if (oisp_json_numbers(oisp_json_get(e, "ccm"), v, 12) != 12)
			return tfail(err, errlen, "awb.illuminants[%d].ccm: 3x4 expected", k);
		for (i = 0; i < 12; i++)
			il->ccm[i / 4][i % 4] = (int)v[i];
		if (k && il->cct <= p->ill[k - 1].cct)
			return tfail(err, errlen, "awb.illuminants must be sorted by cct");
	}
	p->n_illum = k;
	return 0;
}

int oisp_tuning_apply_json(struct oisp_tuning *t, const struct oisp_json *root, char *err, size_t errlen)
{
	const struct oisp_json *m;

	if (!root || root->type != OJ_OBJ)
		return tfail(err, errlen, "tuning: object expected");
	for (m = root->child; m; m = m->next) {
		if (!strcmp(m->key, "version") || !strcmp(m->key, "comment") || !strcmp(m->key, "meta"))
			continue;
		if (m->type != OJ_OBJ)
			return tfail(err, errlen, "tuning.%s: object expected", m->key);
		if (!strcmp(m->key, "isp")) {
			if (set_cfg(&t->cfg, m, err, errlen))
				return -1;
		} else if (!strcmp(m->key, "ae")) {
			const struct oisp_json *gt = oisp_json_get(m, "gain_table");

			if (set_nums(&t->ae, ae_desc, m, "ae", "gain_table", err, errlen))
				return -1;
			if (gt && set_gain_table(&t->ae, gt, err, errlen))
				return -1;
		} else if (!strcmp(m->key, "awb")) {
			const struct oisp_json *il = oisp_json_get(m, "illuminants");

			if (set_nums(&t->awb, awb_desc, m, "awb", "illuminants", err, errlen))
				return -1;
			if (il && set_illuminants(&t->awb, il, err, errlen))
				return -1;
		} else if (!strcmp(m->key, "af")) {
			if (set_nums(&t->af, af_desc, m, "af", NULL, err, errlen))
				return -1;
		} else if (!strcmp(m->key, "sensor")) {
			if (set_sensor(t, m, err, errlen))
				return -1;
		} else {
			return tfail(err, errlen, "tuning: unknown section \"%s\"", m->key);
		}
	}
	return 0;
}

int oisp_tuning_load(struct oisp_tuning *t, const char *path, char *err, size_t errlen)
{
	struct oisp_json *j = oisp_json_load(path, err, errlen);
	int r;

	if (!j)
		return -1;
	r = oisp_tuning_apply_json(t, j, err, errlen);
	oisp_json_free(j);
	return r;
}
