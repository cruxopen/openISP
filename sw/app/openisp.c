// SPDX-License-Identifier: MIT
/*
 * openisp - command line tool and 3A daemon of the openISP hardware ISP.
 *
 *   openisp [-d DEV] info
 *   openisp [-d DEV] apply TUNING.json [--no-tables]
 *   openisp [-d DEV] stats [-n N] [--save DIR]
 *   openisp [-d DEV] reg ADDR [VALUE]
 *   openisp [-d DEV] run TUNING.json [--sensor NAME] [--listen unix:PATH|[HOST:]PORT] [--dgain-only] [-n N] [-v]
 *   openisp pack TUNING.json [--width W] [--height H]
 *
 * DEV: /dev/v4l-subdevN (default: the sub-device named "openisp"),
 *      mem:0xA0000000 (registers through /dev/mem), sim:LOG:DIR:WxH (host test).
 */
#include <arpa/inet.h>
#include <ctype.h>
#include <errno.h>
#include <math.h>
#include <netinet/in.h>
#include <poll.h>
#include <signal.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

#include "oisp.h"

#define ERRLEN 256

static volatile sig_atomic_t stop;
static void on_signal(int s) { (void)s; stop = 1; }

static void die(const char *msg)
{
	fprintf(stderr, "openisp: %s\n", msg);
	exit(1);
}

static void usage(void)
{
	fputs("usage: openisp [-d DEV] info | apply TUNING [--no-tables] | stats [-n N] [--save DIR]\n"
	      "               | reg ADDR [VALUE] | run TUNING [--sensor NAME] [--listen unix:PATH|[HOST:]PORT]\n"
	      "                 [--dgain-only] [-n N] [-v]\n"
	      "       openisp pack TUNING [--width W] [--height H]\n"
	      "DEV: /dev/v4l-subdevN | mem:PHYS_BASE | sim:LOG:STATS_DIR:WxH\n", stderr);
	exit(2);
}

static struct oisp_hal *open_hal(const char *dev)
{
	char err[ERRLEN];
	struct oisp_hal *h = oisp_hal_open(dev, err, sizeof(err));

	if (!h)
		die(err);
	return h;
}

static void load_tuning(struct oisp_tuning *t, const char *path)
{
	char err[ERRLEN];

	oisp_tuning_default(t);
	if (oisp_tuning_load(t, path, err, sizeof(err)))
		die(err);
}

/* frame size and Bayer order come from the driver (sink pad format) */
static void take_format(struct oisp_hal *h, struct oisp_cfg *c)
{
	static const struct { uint32_t code; const char *pat; } codes[] = {
		{ 0x300f, "rggb" }, { 0x3007, "bggr" }, { 0x300a, "grbg" }, { 0x300e, "gbrg" },
	};
	struct openisp_info info;
	unsigned int i;

	if (h->ops->info(h, &info) || !info.width || !info.height)
		return;
	c->width = info.width;
	c->height = info.height;
	for (i = 0; i < sizeof(codes) / sizeof(codes[0]); i++)
		if (codes[i].code == info.mbus_code)
			strcpy(c->bayer_pattern, codes[i].pat);
}

/* ------------------------------------------------------------------ commands */
static int cmd_info(struct oisp_hal *h)
{
	struct openisp_info i;
	int r = h->ops->info(h, &i);

	if (r)
		die(strerror(-r));
	printf("id         0x%08x%s\n", i.id, i.id == OISP_ID_VALUE ? " (openISP)" : " (no hardware)");
	printf("version    %u.%u, max width %u\n", i.version >> 24, (i.version >> 16) & 0xff, i.version & 0xffff);
	printf("format     %ux%u, media bus code 0x%04x\n", i.width, i.height, i.mbus_code);
	printf("frames     in %u, out %u\n", i.frames_in, i.frames_out);
	return 0;
}

static int cmd_apply(struct oisp_hal *h, const char *path, int tables)
{
	struct oisp_tuning t;
	char err[ERRLEN];

	load_tuning(&t, path);
	take_format(h, &t.cfg);
	if (oisp_hal_apply(h, &t.cfg, tables, err, sizeof(err)))
		die(err);
	printf("applied %s (%dx%d %s)\n", path, t.cfg.width, t.cfg.height, t.cfg.bayer_pattern);
	return 0;
}

static void stats_summary(const struct openisp_stats *st, FILE *f)
{
	uint64_t r = 0, g = 0, b = 0, n = 0, l = 0, af = 0, hist = 0;
	int z;

	for (z = 0; z < OPENISP_NZONE; z++) {
		r += st->awb_r[z]; g += st->awb_g[z]; b += st->awb_b[z]; n += st->awb_n[z];
		l += st->ae_sum[z]; af += st->af[z]; hist += st->ae_hist[z];
	}
	fprintf(f, "frame %u: awb r/g %.4f b/g %.4f (%llu quads), ae sum %llu (%llu quads), af %llu\n",
		st->frame, g ? 2.0 * r / g : 0.0, g ? 2.0 * b / g : 0.0, (unsigned long long)n,
		(unsigned long long)l, (unsigned long long)hist, (unsigned long long)af);
}

static int cmd_stats(struct oisp_hal *h, int count, const char *save)
{
	static struct openisp_stats st;
	uint32_t last = 0;
	uint32_t v;
	int i, r;

	if (!h->ops->read(h, OISP_ST_BAYER_FRAMES, &v))
		last = v;
	for (i = 0; i < count && !stop; i++) {
		r = h->ops->stats(h, &st, last, 2000);
		if (r)
			die(strerror(-r));
		last = st.frame;
		stats_summary(&st, stdout);
		if (save) {
			char p[600];
			FILE *f;

			mkdir(save, 0755);
			snprintf(p, sizeof(p), "%s/stats_%u.bin", save, st.frame);
			f = fopen(p, "wb");
			if (!f || fwrite(&st, sizeof(st), 1, f) != 1)
				die("cannot write the statistics file");
			fclose(f);
		}
	}
	return 0;
}

static int cmd_reg(struct oisp_hal *h, const char *a, const char *v)
{
	uint32_t addr = strtoul(a, NULL, 0), val;
	int r;

	if (v) {
		struct openisp_reg_write w = { addr, (uint32_t)strtoul(v, NULL, 0) };

		r = h->ops->write(h, &w, 1, 0);
	} else {
		r = h->ops->read(h, addr, &val);
		if (!r)
			printf("0x%04x: 0x%08x\n", addr, val);
	}
	if (r)
		die(strerror(-r));
	return 0;
}

static int cmd_pack(const char *path, int w, int hgt)
{
	struct oisp_tuning t;
	char err[ERRLEN];
	uint32_t words[OISP_NREG];
	int i;

	load_tuning(&t, path);
	if (w)
		t.cfg.width = w;
	if (hgt)
		t.cfg.height = hgt;
	if (oisp_cfg_words(&t.cfg, words, err, sizeof(err)))
		die(err);
	oisp_cfg_tables(&t.cfg);
	for (i = 0; i < OISP_NREG; i++)
		printf("W %x %x\n", i, words[i]);
	for (i = 0; i < OISP_GAMMA_SIZE; i++)
		printf("W %x %x\n", OISP_GAMMA_BASE + i, t.cfg.gamma_lut[i]);
	for (i = 0; i < OISP_NLM_LUT_SIZE; i++)
		printf("W %x %x\n", OISP_NLM_LUT_BASE + i, t.cfg.nlm_lut[i]);
	return 0;
}

/* ------------------------------------------------------------------ live tuning server */
struct live {
	int lfd, cfd;
	char buf[65536];
	size_t len;
};

/* "unix:/path" (default for the service: access by file permissions) or [HOST:]PORT */
static int live_listen(struct live *lv, const char *spec)
{
	struct sockaddr_in a;
	const char *colon = strrchr(spec, ':');
	char host[64] = "127.0.0.1";
	int one = 1;

	lv->cfd = -1;
	if (!strncmp(spec, "unix:", 5)) {
		struct sockaddr_un u;

		memset(&u, 0, sizeof(u));
		u.sun_family = AF_UNIX;
		if (strlen(spec + 5) >= sizeof(u.sun_path))
			return -1;
		strcpy(u.sun_path, spec + 5);
		unlink(u.sun_path);
		lv->lfd = socket(AF_UNIX, SOCK_STREAM, 0);
		if (lv->lfd < 0 || bind(lv->lfd, (struct sockaddr *)&u, sizeof(u)) || listen(lv->lfd, 1))
			return -1;
		chmod(u.sun_path, 0660);
		fprintf(stderr, "openisp: live tuning on %s\n", u.sun_path);
		return 0;
	}
	memset(&a, 0, sizeof(a));
	if (colon) {
		snprintf(host, sizeof(host), "%.*s", (int)(colon - spec), spec);
		spec = colon + 1;
	}
	a.sin_family = AF_INET;
	a.sin_port = htons((uint16_t)atoi(spec));
	if (inet_pton(AF_INET, host, &a.sin_addr) != 1)
		return -1;
	lv->lfd = socket(AF_INET, SOCK_STREAM, 0);
	if (lv->lfd < 0)
		return -1;
	setsockopt(lv->lfd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
	if (bind(lv->lfd, (struct sockaddr *)&a, sizeof(a)) || listen(lv->lfd, 1))
		return -1;
	lv->cfd = -1;
	fprintf(stderr, "openisp: live tuning on %s:%d\n", host, ntohs(a.sin_port));
	return 0;
}

struct daemon {
	struct oisp_hal *h;
	struct oisp_tuning t;
	struct oisp_grid grid;
	struct oisp_sensor sensor;
	struct oisp_awb_state awb;
	struct oisp_ae_out ae;
	struct oisp_af_state af;
	struct openisp_stats st;
	double e_used, dg_used;
	int dgain_only, fixed_lines;		/* no sensor control: AE through the WB gains */
	uint32_t last_ae_frame;
	int have_stats, dirty, tables_dirty, verbose;
	struct live lv;
};

static void reply(struct live *lv, const char *fmt, ...)
{
	char out[20000];
	va_list ap;
	int n;

	va_start(ap, fmt);
	n = vsnprintf(out, sizeof(out) - 1, fmt, ap);
	va_end(ap);
	if (n < 0)
		return;
	if (n > (int)sizeof(out) - 2)
		n = sizeof(out) - 2;
	out[n++] = '\n';
	if (write(lv->cfd, out, n) < 0)
		lv->cfd = -1;
}

static void send_stats(struct daemon *d)
{
	const struct openisp_stats *s = &d->st;
	static char out[60000];
	const uint32_t *arr[7] = { s->awb_r, s->awb_g, s->awb_b, s->awb_n, s->ae_sum, s->ae_hist, s->af };
	const char *nm[7] = { "awb_r", "awb_g", "awb_b", "awb_n", "ae_sum", "ae_hist", "af" };
	size_t p = 0;
	int k, i;

	p += snprintf(out + p, sizeof(out) - p, "ok {\"frame\": %u", s->frame);
	for (k = 0; k < 7; k++) {
		p += snprintf(out + p, sizeof(out) - p, ", \"%s\": [", nm[k]);
		for (i = 0; i < 256; i++)
			p += snprintf(out + p, sizeof(out) - p, "%s%u", i ? "," : "", arr[k][i]);
		p += snprintf(out + p, sizeof(out) - p, "]");
	}
	p += snprintf(out + p, sizeof(out) - p, "}\n");
	if (write(d->lv.cfd, out, p) < 0)
		d->lv.cfd = -1;
}

/* the frame format always comes from the driver; the grid follows the config */
static int refresh(struct daemon *d, char *err, size_t errlen)
{
	struct oisp_grid g;

	take_format(d->h, &d->t.cfg);
	if (oisp_zone_grid(d->t.cfg.width, d->t.cfg.height, d->t.cfg.stats_x0, d->t.cfg.stats_y0,
			   d->t.cfg.stats_zw, d->t.cfg.stats_zh, &g, err, errlen))
		return -1;
	d->grid = g;
	return 0;
}

static void live_command(struct daemon *d, char *line)
{
	char err[ERRLEN], val[8192];
	char *cmd = strtok(line, " \t\r"), *arg = strtok(NULL, " \t\r"), *rest = strtok(NULL, "\r");

	if (!cmd)
		return;
	if (!strcmp(cmd, "get") && arg) {
		if (oisp_cfg_get(&d->t.cfg, arg, val, sizeof(val)))
			reply(&d->lv, "err unknown parameter %s", arg);
		else
			reply(&d->lv, "ok %s", val);
	} else if (!strcmp(cmd, "set") && arg && rest) {
		struct oisp_cfg old = d->t.cfg;

		if (oisp_cfg_set(&d->t.cfg, arg, rest, err, sizeof(err)) || refresh(d, err, sizeof(err))) {
			d->t.cfg = old;
			reply(&d->lv, "err %s", err);
		} else {
			d->dirty = 1;
			if (!strcmp(arg, "gamma") || !strcmp(arg, "gamma_lut") || !strcmp(arg, "nlm_h") ||
			    !strcmp(arg, "nlm_dshift") || !strcmp(arg, "nlm_lut"))
				d->tables_dirty = 1;
			reply(&d->lv, "ok");
		}
	} else if (!strcmp(cmd, "aaa") && arg && rest) {
		int on = !strncmp(rest, "on", 2);

		if (!strcmp(arg, "ae"))
			d->t.ae.enable = on;
		else if (!strcmp(arg, "awb"))
			d->t.awb.enable = on;
		else if (!strcmp(arg, "af"))
			d->t.af.enable = on;
		else {
			reply(&d->lv, "err aaa ae|awb|af on|off");
			return;
		}
		reply(&d->lv, "ok");
	} else if (!strcmp(cmd, "stats")) {
		if (!d->have_stats)
			reply(&d->lv, "err no statistics yet");
		else
			send_stats(d);
	} else if (!strcmp(cmd, "state")) {
		reply(&d->lv, "ok {\"frame\": %u, \"ae\": {\"enable\": %d, \"mean\": %.3f, \"lines\": %d, "
		      "\"gain_code\": %d, \"again\": %.4f, \"dgain\": %.4f, \"exposure\": %.3f}, "
		      "\"awb\": {\"enable\": %d, \"gain_r\": %d, \"gain_b\": %d, \"cct\": %.1f}, "
		      "\"af\": {\"enable\": %d, \"phase\": %d, \"pos\": %d, \"score\": %llu}}",
		      d->st.frame, d->t.ae.enable, d->ae.mean, d->ae.lines, d->ae.gain_code, d->ae.again,
		      d->ae.dgain, d->ae.exposure, d->t.awb.enable, d->awb.gain_r, d->awb.gain_b, d->awb.cct,
		      d->t.af.enable, d->af.phase, d->af.pos, (unsigned long long)d->af.score);
	} else if (!strcmp(cmd, "dump")) {
		FILE *m;
		char *buf = NULL;
		size_t n = 0;

		m = open_memstream(&buf, &n);
		if (!m) {
			reply(&d->lv, "err out of memory");
			return;
		}
		oisp_cfg_write_json(&d->t.cfg, m);
		fclose(m);
		reply(&d->lv, "ok %s", buf);
		free(buf);
	} else if (!strcmp(cmd, "load") && arg) {
		struct oisp_json *j;

		if (rest)
			arg[strlen(arg)] = ' ';		/* undo strtok: the JSON text may contain blanks */
		struct oisp_tuning old = d->t;

		j = oisp_json_parse(arg, err, sizeof(err));
		if (!j || oisp_tuning_apply_json(&d->t, j, err, sizeof(err)) || refresh(d, err, sizeof(err))) {
			d->t = old;
			reply(&d->lv, "err %s", err);
		} else {
			d->dirty = d->tables_dirty = 1;
			reply(&d->lv, "ok");
		}
		oisp_json_free(j);
	} else {
		reply(&d->lv, "err commands: get NAME | set NAME VALUES | aaa ae|awb|af on|off | "
		      "stats | state | dump | load {json}");
	}
}

static void live_poll(struct daemon *d)
{
	struct pollfd p[2];
	int n = 0;

	if (d->lv.lfd < 0)
		return;
	p[n].fd = d->lv.lfd;
	p[n++].events = POLLIN;
	if (d->lv.cfd >= 0) {
		p[n].fd = d->lv.cfd;
		p[n++].events = POLLIN;
	}
	if (poll(p, n, 0) <= 0)
		return;
	if (p[0].revents & POLLIN) {
		int c = accept(d->lv.lfd, NULL, NULL);

		if (c >= 0) {
			if (d->lv.cfd >= 0)
				close(d->lv.cfd);	/* one client at a time: the newest wins */
			d->lv.cfd = c;
			d->lv.len = 0;
			return;
		}
	}
	if (n > 1 && (p[1].revents & (POLLIN | POLLHUP))) {
		ssize_t r = read(d->lv.cfd, d->lv.buf + d->lv.len, sizeof(d->lv.buf) - 1 - d->lv.len);
		char *nl;

		if (r <= 0) {
			close(d->lv.cfd);
			d->lv.cfd = -1;
			return;
		}
		d->lv.len += r;
		d->lv.buf[d->lv.len] = 0;
		while ((nl = strchr(d->lv.buf, '\n'))) {
			size_t used = nl - d->lv.buf + 1;

			*nl = 0;
			live_command(d, d->lv.buf);
			if (d->lv.cfd < 0)
				return;
			memmove(d->lv.buf, d->lv.buf + used, d->lv.len - used + 1);
			d->lv.len -= used;
		}
		if (d->lv.len == sizeof(d->lv.buf) - 1)
			d->lv.len = 0;		/* line too long: drop it */
	}
}

/* ------------------------------------------------------------------ 3A loop */
static void frame(struct daemon *d)
{
	struct oisp_tuning *t = &d->t;
	char err[ERRLEN];
	int wb[4], i;

	if (t->awb.enable && !oisp_awb_step(&t->awb, &d->awb, &d->st)) {
		t->cfg.awb_gain[0] = d->awb.gain_r;	/* base gains, digital gain added below */
		t->cfg.awb_gain[3] = d->awb.gain_b;
		if (d->awb.ccm_valid)
			memcpy(t->cfg.ccm, d->awb.ccm, sizeof(t->cfg.ccm));
	}
	if (t->ae.enable && d->st.frame >= d->last_ae_frame + t->ae.delay + 1) {
		double e = oisp_ae_target(&t->ae, &d->st, &d->grid, d->e_used, d->dg_used, NULL);

		d->ae.mean = oisp_ae_measure(&d->st, &d->grid) * d->dg_used;
		oisp_ae_split(&t->ae, e, &d->ae);
		if (d->dgain_only) {
			double dg = e / d->fixed_lines;

			d->ae.lines = d->fixed_lines;
			d->ae.gain_code = t->ae.n_gain ? t->ae.gain_code_min : t->ae.gain_1x;
			d->ae.again = 1.0;
			d->ae.dgain = dg < 0.25 ? 0.25 : dg > t->ae.dgain_max ? t->ae.dgain_max : dg;
			d->ae.exposure = d->fixed_lines * d->ae.dgain;
		}
		d->e_used = d->ae.exposure;
		d->dg_used = d->ae.dgain;
		d->last_ae_frame = d->st.frame;
		if (d->sensor.fd >= 0)
			oisp_sensor_set(&d->sensor, d->ae.lines, d->ae.gain_code);
	}
	if (t->af.enable) {
		int pos = oisp_af_step(&t->af, &d->af, oisp_af_score(&t->af, &d->st));

		if (pos >= 0 && d->sensor.fd >= 0)
			oisp_sensor_focus(&d->sensor, pos);
	}
	/* registers of the next frame: WB gains include the digital gain */
	{
		struct oisp_cfg c = t->cfg;

		oisp_wb_registers(t->cfg.awb_gain[0], t->cfg.awb_gain[3], d->dg_used, wb);
		if (t->ae.enable)
			for (i = 0; i < 4; i++)
				c.awb_gain[i] = wb[i];
		else
			memcpy(wb, c.awb_gain, sizeof(wb));
		if (oisp_hal_apply(d->h, &c, d->tables_dirty, err, sizeof(err)))
			fprintf(stderr, "openisp: %s\n", err);
		d->tables_dirty = 0;
		d->dirty = 0;
	}
	if (d->verbose) {
		printf("F %u mean %.6f lines %d code %d dgain %.9f wb %d %d %d %d ccm",
		       d->st.frame, d->ae.mean, d->ae.lines, d->ae.gain_code, d->ae.dgain,
		       wb[0], wb[1], wb[2], wb[3]);
		for (i = 0; i < 12; i++)
			printf(" %d", t->cfg.ccm[i / 4][i % 4]);
		printf(" cct %.3f af %llu\n", d->awb.cct, (unsigned long long)d->af.score);
		fflush(stdout);
	}
}

static int cmd_run(const char *dev, const char *path, const char *sensor, const char *listen_on,
		   int count, int verbose, int force_dgain)
{
	static struct daemon d;
	char err[ERRLEN];
	uint32_t last = 0, v;
	int n = 0, r;

	memset(&d, 0, sizeof(d));
	d.h = open_hal(dev);
	d.lv.lfd = d.lv.cfd = -1;
	d.sensor.fd = -1;
	d.verbose = verbose;
	load_tuning(&d.t, path);
	take_format(d.h, &d.t.cfg);
	if (oisp_zone_grid(d.t.cfg.width, d.t.cfg.height, d.t.cfg.stats_x0, d.t.cfg.stats_y0,
			   d.t.cfg.stats_zw, d.t.cfg.stats_zh, &d.grid, err, sizeof(err)))
		die(err);
	if (!sensor && d.t.sensor[0])
		sensor = d.t.sensor;
	if (sensor && oisp_sensor_open(&d.sensor, sensor, d.t.exposure_ctrl, d.t.gain_ctrl, err, sizeof(err)))
		fprintf(stderr, "openisp: %s\n", err);
	if (d.sensor.fd >= 0)
		fprintf(stderr, "openisp: sensor %s: exposure %s [%d..%d], gain %s [%d..%d]\n", sensor,
			d.sensor.has_exposure ? d.sensor.exp_name : "-", d.sensor.exp_min, d.sensor.exp_max,
			d.sensor.has_gain ? d.sensor.gain_name : "-", d.sensor.gain_min, d.sensor.gain_max);
	if (d.sensor.fd >= 0) {			/* limits of the sensor driver */
		struct oisp_ae_param *a = &d.t.ae;

		if (d.sensor.has_exposure) {
			a->lines_min = a->lines_min > d.sensor.exp_min ? a->lines_min : d.sensor.exp_min;
			a->lines_max = a->lines_max < d.sensor.exp_max ? a->lines_max : d.sensor.exp_max;
		}
		if (d.sensor.has_gain) {
			a->gain_code_min = a->gain_code_min > d.sensor.gain_min ? a->gain_code_min : d.sensor.gain_min;
			a->gain_code_max = a->gain_code_max < d.sensor.gain_max ? a->gain_code_max : d.sensor.gain_max;
		} else if (a->n_gain) {
			a->gain_code_max = a->gain_code_min;
		} else {
			a->gain_code_min = a->gain_code_max = a->gain_1x;
		}
	}
	/* the simulation backend plays the sensor; on hardware without exposure
	 * control the exposure stays fixed and AE works through the WB gains */
	d.dgain_only = force_dgain || (!d.h->log && (d.sensor.fd < 0 || !d.sensor.has_exposure));
	if (d.dgain_only && d.t.ae.enable)
		fprintf(stderr, "openisp: no sensor exposure control, AE uses the digital gain only\n");
	if (listen_on && live_listen(&d.lv, listen_on))
		die("cannot open the live tuning socket");
	/* initial exposure: middle of the integration range at 1x gain */
	oisp_ae_split(&d.t.ae, 0.5 * (d.t.ae.lines_min + d.t.ae.lines_max), &d.ae);
	d.fixed_lines = d.ae.lines;
	d.e_used = d.ae.exposure;
	d.dg_used = d.ae.dgain;
	if (d.sensor.fd >= 0)
		oisp_sensor_set(&d.sensor, d.ae.lines, d.ae.gain_code);
	if (oisp_hal_apply(d.h, &d.t.cfg, 1, err, sizeof(err)))
		die(err);
	if (!d.h->ops->read(d.h, OISP_ST_BAYER_FRAMES, &v))
		last = v;
	signal(SIGINT, on_signal);
	signal(SIGTERM, on_signal);
	signal(SIGPIPE, SIG_IGN);
	while (!stop && (!count || n < count)) {
		live_poll(&d);
		r = d.h->ops->stats(d.h, &d.st, last, 100);
		if (r == -ETIMEDOUT || r == -EAGAIN || r == -EINTR) {
			if (d.dirty || d.tables_dirty) {	/* stream stopped: apply now */
				if (oisp_hal_apply(d.h, &d.t.cfg, d.tables_dirty, err, sizeof(err)))
					fprintf(stderr, "openisp: %s\n", err);
				d.dirty = d.tables_dirty = 0;
			}
			continue;
		}
		if (r == -ENOENT)		/* simulation: no more frames */
			break;
		if (r)
			die(strerror(-r));
		if (d.have_stats && d.st.frame < last)	/* stream restarted (CTRL.RESET) */
			d.last_ae_frame = 0;
		last = d.st.frame;
		d.have_stats = 1;
		frame(&d);
		n++;
	}
	oisp_sensor_close(&d.sensor);
	oisp_hal_close(d.h);
	return 0;
}

int main(int argc, char **argv)
{
	const char *dev = NULL, *sensor = NULL, *listen_on = NULL, *save = NULL;
	int i = 1, count = 0, verbose = 0, tables = 1, w = 0, hgt = 0, dgain_only = 0;
	const char *cmd, *arg1 = NULL, *arg2 = NULL;

	if (i + 1 < argc && !strcmp(argv[i], "-d")) {
		dev = argv[i + 1];
		i += 2;
	}
	if (i >= argc)
		usage();
	cmd = argv[i++];
	for (; i < argc; i++) {
		if (!strcmp(argv[i], "-n") && i + 1 < argc)
			count = atoi(argv[++i]);
		else if (!strcmp(argv[i], "-v"))
			verbose = 1;
		else if (!strcmp(argv[i], "--no-tables"))
			tables = 0;
		else if (!strcmp(argv[i], "--dgain-only"))
			dgain_only = 1;
		else if (!strcmp(argv[i], "--sensor") && i + 1 < argc)
			sensor = argv[++i];
		else if (!strcmp(argv[i], "--listen") && i + 1 < argc)
			listen_on = argv[++i];
		else if (!strcmp(argv[i], "--save") && i + 1 < argc)
			save = argv[++i];
		else if (!strcmp(argv[i], "--width") && i + 1 < argc)
			w = atoi(argv[++i]);
		else if (!strcmp(argv[i], "--height") && i + 1 < argc)
			hgt = atoi(argv[++i]);
		else if (argv[i][0] == '-' && argv[i][1] && !isdigit((unsigned char)argv[i][1]))
			usage();
		else if (!arg1)
			arg1 = argv[i];
		else if (!arg2)
			arg2 = argv[i];
		else
			usage();
	}
	if (!strcmp(cmd, "pack") && arg1)
		return cmd_pack(arg1, w, hgt);
	if (!strcmp(cmd, "run") && arg1)
		return cmd_run(dev, arg1, sensor, listen_on, count, verbose, dgain_only);
	if (!strcmp(cmd, "info"))
		return cmd_info(open_hal(dev));
	if (!strcmp(cmd, "apply") && arg1)
		return cmd_apply(open_hal(dev), arg1, tables);
	if (!strcmp(cmd, "stats"))
		return cmd_stats(open_hal(dev), count ? count : 1, save);
	if (!strcmp(cmd, "reg") && arg1)
		return cmd_reg(open_hal(dev), arg1, arg2);
	usage();
	return 2;
}
