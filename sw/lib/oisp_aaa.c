// SPDX-License-Identifier: MIT
/*
 * AE / AWB / AF control loops.  Mirror of the "firmware reference" part of
 * hwmodel/aaa.py, operation for operation (same summation order), so the
 * results can be compared with the Python reference (tests/test_fw.py).
 */
#include <math.h>
#include <string.h>

#include "oisp.h"

static double clampd(double x, double lo, double hi)
{
	return x < lo ? lo : x > hi ? hi : x;
}

static int clampi(int x, int lo, int hi)
{
	return x < lo ? lo : x > hi ? hi : x;
}

void oisp_ae_default(struct oisp_ae_param *p)
{
	memset(p, 0, sizeof(*p));
	p->enable = 1;
	p->target = 184.0;
	p->damping = 0.5;
	p->ratio_max = 4.0;
	p->hl_bin = 250;
	p->hl_frac = 0.02;
	p->hl_scale = 0.7;
	p->lines_min = 8;
	p->lines_max = 2000;
	p->gain_1x = 16;
	p->gain_code_min = 16;
	p->gain_code_max = 128;
	p->dgain_max = 4.0;
	p->delay = 2;
}

void oisp_awb_default(struct oisp_awb_param *p)
{
	memset(p, 0, sizeof(*p));
	p->enable = 1;
	p->min_quads = 16;
	p->speed = 0.5;
	p->locus_dist = 0.25;
	p->gain_min = 256;
	p->gain_max = 8191;
}

void oisp_af_default(struct oisp_af_param *p)
{
	memset(p, 0, sizeof(*p));
	p->roi[0] = 4; p->roi[1] = 12; p->roi[2] = 4; p->roi[3] = 12;
	p->pos_min = 0;
	p->pos_max = 1023;
	p->step = 32;
}

/* ------------------------------------------------------------------ AE */
double oisp_ae_measure(const struct openisp_stats *st, const struct oisp_grid *g)
{
	double qn = (double)((g->zw / 2) * (g->zh / 2));
	double c = (OISP_NZ - 1) / 2.0, num = 0.0, den = 0.0;
	int zy, zx;

	for (zy = 0; zy < OISP_NZ; zy++)
		for (zx = 0; zx < OISP_NZ; zx++) {
			double d = sqrt((zy - c) * (zy - c) + (zx - c) * (zx - c)) / (OISP_NZ / 2.0);
			double w = 1.0 + 3.0 * clampd(1.0 - d, 0.0, 1.0);

			num += w * (st->ae_sum[zy * OISP_NZ + zx] / qn);
			den += w;
		}
	return num / den;
}

double oisp_ae_target(const struct oisp_ae_param *p, const struct openisp_stats *st,
		      const struct oisp_grid *g, double e_used, double dg_used, double *mean)
{
	double m = oisp_ae_measure(st, g) * dg_used, target, ratio;
	uint64_t tot = 0, hi = 0;
	int i;

	if (mean)
		*mean = m;
	if (m < 1.0)
		m = 1.0;
	for (i = 0; i < OPENISP_HIST_BINS; i++) {
		tot += st->ae_hist[i];
		if (i >= p->hl_bin)
			hi += st->ae_hist[i];
	}
	target = p->target * ((double)hi > p->hl_frac * (double)tot ? p->hl_scale : 1.0);
	ratio = pow(target / m, p->damping);
	ratio = clampd(ratio, 1.0 / p->ratio_max, p->ratio_max);
	return e_used * ratio;
}

void oisp_ae_split(const struct oisp_ae_param *p, double e, struct oisp_ae_out *o)
{
	double lines = clampd(e, p->lines_min, p->lines_max), g;
	int code;

	if (p->flicker_lines && lines >= p->flicker_lines)
		lines = floor(lines / p->flicker_lines) * p->flicker_lines;
	o->lines = (int)floor(lines + 0.5);
	g = e / o->lines;
	if (p->n_gain > 0) {		/* largest table gain <= g, rest is digital */
		int lo = clampi(p->gain_code_min, 0, p->n_gain - 1);
		int hi = clampi(p->gain_code_max, lo, p->n_gain - 1);

		for (code = lo; code < hi && p->gain_table[code + 1] <= g * (1 + 1e-9); code++)
			;
		o->gain_code = code;
		o->again = p->gain_table[code];
	} else {
		code = (int)floor(g * p->gain_1x + 0.5);
		o->gain_code = clampi(code, p->gain_code_min, p->gain_code_max);
		o->again = (double)o->gain_code / p->gain_1x;
	}
	o->dgain = clampd(g / o->again, 1.0, p->dgain_max);
	o->exposure = o->lines * o->again * o->dgain;
}

/* ------------------------------------------------------------------ AWB */
static int awb_measure(const struct openisp_stats *st, int min_quads, double *lr, double *lb)
{
	uint64_t r = 0, g = 0, b = 0;
	int z;

	for (z = 0; z < OPENISP_NZONE; z++)
		if ((int64_t)st->awb_n[z] >= min_quads) {
			r += st->awb_r[z];
			g += st->awb_g[z];
			b += st->awb_b[z];
		}
	if (!r || !g || !b)
		return -1;
	*lr = log2((double)g / 2.0 / (double)r);
	*lb = log2((double)g / 2.0 / (double)b);
	return 0;
}

static void awb_locus(const struct oisp_awb_param *p, double *x, double *y, double max_dist,
		      int *kout, double *fout)
{
	double bd = 0, bf = 0, bx = 0, by = 0;
	int k, bk = -1;

	if (p->n_illum < 2) {
		*kout = 0;
		*fout = 0.0;
		return;
	}
	for (k = 0; k < p->n_illum - 1; k++) {
		double ax = log2(p->ill[k].gain_r / 1024.0), ay = log2(p->ill[k].gain_b / 1024.0);
		double dx = log2(p->ill[k + 1].gain_r / 1024.0) - ax;
		double dy = log2(p->ill[k + 1].gain_b / 1024.0) - ay;
		double L = dx * dx + dy * dy;
		double f = L == 0 ? 0.0 : ((*x - ax) * dx + (*y - ay) * dy) / L;
		double px, py, d;

		f = clampd(f, 0.0, 1.0);
		px = ax + f * dx;
		py = ay + f * dy;
		d = sqrt((*x - px) * (*x - px) + (*y - py) * (*y - py));
		if (bk < 0 || d < bd) {
			bd = d; bk = k; bf = f; bx = px; by = py;
		}
	}
	if (bd > max_dist) {
		double s = max_dist / bd;

		*x = bx + (*x - bx) * s;
		*y = by + (*y - by) * s;
	}
	*kout = bk;
	*fout = bf;
}

static void ccm_interp(const struct oisp_awb_param *p, int k, double f, int out[3][4])
{
	const int (*a)[4] = p->ill[k].ccm;
	const int (*b)[4] = p->ill[k + 1 < p->n_illum ? k + 1 : p->n_illum - 1].ccm;
	int r, c;

	for (r = 0; r < 3; r++) {
		int sa = a[r][0] + a[r][1] + a[r][2], sb = b[r][0] + b[r][1] + b[r][2], want;

		for (c = 0; c < 4; c++)
			out[r][c] = (int)floor(a[r][c] + f * (b[r][c] - a[r][c]) + 0.5);
		want = (int)floor(sa + f * (sb - sa) + 0.5);
		out[r][r] += want - (out[r][0] + out[r][1] + out[r][2]);
	}
}

int oisp_awb_step(const struct oisp_awb_param *p, struct oisp_awb_state *s,
		  const struct openisp_stats *st)
{
	double lr, lb;

	if (!awb_measure(st, p->min_quads, &lr, &lb)) {
		if (p->n_illum)
			awb_locus(p, &lr, &lb, p->locus_dist, &s->k, &s->f);
		if (!s->valid) {
			s->lr = lr;
			s->lb = lb;
			s->valid = 1;
		} else {
			s->lr += p->speed * (lr - s->lr);
			s->lb += p->speed * (lb - s->lb);
		}
	}
	if (!s->valid)
		return -1;
	s->ccm_valid = 0;
	s->cct = 0.0;
	if (p->n_illum) {
		double x = s->lr, y = s->lb;

		awb_locus(p, &x, &y, 1e9, &s->k, &s->f);
		ccm_interp(p, s->k, s->f, s->ccm);
		s->ccm_valid = 1;
		if (p->n_illum == 1) {
			s->cct = p->ill[0].cct;
		} else {
			double m = (1 - s->f) * 1e6 / p->ill[s->k].cct + s->f * 1e6 / p->ill[s->k + 1].cct;

			s->cct = 1e6 / m;
		}
	}
	s->gain_r = clampi((int)floor(1024.0 * pow(2.0, s->lr) + 0.5), p->gain_min, p->gain_max);
	s->gain_b = clampi((int)floor(1024.0 * pow(2.0, s->lb) + 0.5), p->gain_min, p->gain_max);
	return 0;
}

void oisp_wb_registers(int gain_r, int gain_b, double dgain, int out[4])
{
	const int g[4] = { gain_r, 1024, 1024, gain_b };
	int i;

	for (i = 0; i < 4; i++)
		out[i] = clampi((int)floor(g[i] * dgain + 0.5), 0, 16383);
}

/* ------------------------------------------------------------------ AF */
uint64_t oisp_af_score(const struct oisp_af_param *p, const struct openisp_stats *st)
{
	uint64_t s = 0;
	int zy, zx;

	for (zy = p->roi[0]; zy < p->roi[1]; zy++)
		for (zx = p->roi[2]; zx < p->roi[3]; zx++)
			s += st->af[zy * OISP_NZ + zx];
	return s;
}

/*
 * Contrast AF: full sweep pos_min..pos_max, then move to the best position
 * and hold it; a change of the score by more than 30 % restarts the sweep.
 * score is the AF score of the frame taken at the last returned position.
 */
int oisp_af_step(const struct oisp_af_param *p, struct oisp_af_state *s, uint64_t score)
{
	s->score = score;
	switch (s->phase) {
	case 0:
		s->phase = 1;
		s->best = 0;
		s->best_pos = s->pos = p->pos_min;
		return s->pos;
	case 1:
		if (score > s->best) {
			s->best = score;
			s->best_pos = s->pos;
		}
		s->pos += p->step;
		if (s->pos > p->pos_max) {
			s->phase = 2;
			s->locked_score = 0;
			s->pos = s->best_pos;
		}
		return s->pos;
	default:
		if (!s->locked_score) {
			s->locked_score = score ? score : 1;
			return -1;
		}
		if (score * 10 < s->locked_score * 7 || score * 10 > s->locked_score * 13)
			s->phase = 0;
		return -1;
	}
}
