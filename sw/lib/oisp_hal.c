// SPDX-License-Identifier: MIT
/*
 * Hardware access backends:
 *   /dev/v4l-subdevN   the openisp kernel driver (normal operation)
 *   mem:<phys base>    direct register access through /dev/mem (bring-up
 *                      without the driver; statistics are polled)
 *   sim:<log>:<dir>:<W>x<H>
 *                      host simulation: writes are logged to <log>, the
 *                      statistics of frame n are read from <dir>/stats_<n>.bin
 *                      (struct openisp_stats; waits for the file, a file
 *                      <dir>/end stops); used by tests/test_fw.py
 */
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

#include "oisp.h"

#define MMIO_SIZE	(1u << 18)	/* 18-bit AXI4-Lite byte address */

/* ------------------------------------------------------------------ driver */
static int drv_write(struct oisp_hal *h, const struct openisp_reg_write *w, int n, int commit)
{
	while (n > 0) {
		int k = n > OPENISP_MAX_WRITES ? OPENISP_MAX_WRITES : n;
		struct openisp_params p = { 0 };

		p.count = k;
		p.flags = (commit && k == n) ? OPENISP_PARAMS_COMMIT : 0;
		p.writes = (uintptr_t)w;
		if (ioctl(h->fd, OPENISP_IOC_PARAMS, &p) < 0)
			return -errno;
		w += k;
		n -= k;
	}
	return 0;
}

static int drv_read(struct oisp_hal *h, uint32_t addr, uint32_t *v)
{
	struct openisp_reg r = { addr, 0 };

	if (ioctl(h->fd, OPENISP_IOC_READ_REG, &r) < 0)
		return -errno;
	*v = r.value;
	return 0;
}

static int drv_stats(struct oisp_hal *h, struct openisp_stats *st, uint32_t after, int timeout_ms)
{
	st->frame = after;
	st->timeout_ms = timeout_ms;
	if (ioctl(h->fd, OPENISP_IOC_STATS, st) < 0)
		return -errno;
	return 0;
}

static int drv_info(struct oisp_hal *h, struct openisp_info *info)
{
	return ioctl(h->fd, OPENISP_IOC_INFO, info) < 0 ? -errno : 0;
}

static void drv_close(struct oisp_hal *h)
{
	close(h->fd);
}

static const struct oisp_hal_ops drv_ops = { drv_write, drv_read, drv_stats, drv_info, drv_close };

/* ------------------------------------------------------------------ /dev/mem */
static int mem_write(struct oisp_hal *h, const struct openisp_reg_write *w, int n, int commit)
{
	int i;

	if (commit)
		h->mmio[OISP_CTRL] = OISP_CTRL_HOLD;
	for (i = 0; i < n; i++) {
		if (w[i].addr >= MMIO_SIZE / 4)
			return -EINVAL;
		h->mmio[w[i].addr] = w[i].value;
	}
	if (commit)
		h->mmio[OISP_CTRL] = OISP_CTRL_COMMIT;	/* also releases HOLD */
	return 0;
}

static int mem_read(struct oisp_hal *h, uint32_t addr, uint32_t *v)
{
	if (addr >= MMIO_SIZE / 4)
		return -EINVAL;
	*v = h->mmio[addr];
	return 0;
}

static void read_block(volatile uint32_t *m, uint32_t base, uint32_t *dst, int n)
{
	int i;

	for (i = 0; i < n; i++)
		dst[i] = m[base + i];
}

/* copy one frame of statistics; retried if a new frame was published meanwhile */
static int mem_copy_stats(volatile uint32_t *m, struct openisp_stats *st)
{
	int tries;

	for (tries = 0; tries < 4; tries++) {
		uint32_t f0 = m[OISP_ST_BAYER_FRAMES];

		read_block(m, OISP_ST_AWB_R, st->awb_r, OPENISP_NZONE);
		read_block(m, OISP_ST_AWB_G, st->awb_g, OPENISP_NZONE);
		read_block(m, OISP_ST_AWB_B, st->awb_b, OPENISP_NZONE);
		read_block(m, OISP_ST_AWB_N, st->awb_n, OPENISP_NZONE);
		read_block(m, OISP_ST_AE_SUM, st->ae_sum, OPENISP_NZONE);
		read_block(m, OISP_ST_AE_HIST, st->ae_hist, OPENISP_HIST_BINS);
		read_block(m, OISP_ST_AF, st->af, OPENISP_NZONE);
		if (m[OISP_ST_BAYER_FRAMES] == f0 && m[OISP_ST_AF_FRAMES] == f0) {
			st->frame = f0;
			return 0;
		}
	}
	return -EAGAIN;
}

static uint64_t now_ns(void)
{
	struct timespec ts;

	clock_gettime(CLOCK_MONOTONIC, &ts);
	return (uint64_t)ts.tv_sec * 1000000000ull + ts.tv_nsec;
}

static int mem_stats(struct oisp_hal *h, struct openisp_stats *st, uint32_t after, int timeout_ms)
{
	uint64_t end = now_ns() + (uint64_t)timeout_ms * 1000000ull;

	for (;;) {
		uint32_t f = h->mmio[OISP_ST_BAYER_FRAMES];

		if (f != after && h->mmio[OISP_ST_AF_FRAMES] == f) {
			int r = mem_copy_stats(h->mmio, st);

			st->timestamp_ns = now_ns();
			return r;
		}
		if (now_ns() >= end)
			return -ETIMEDOUT;
		usleep(500);
	}
}

static int mem_info(struct oisp_hal *h, struct openisp_info *info)
{
	memset(info, 0, sizeof(*info));
	info->id = h->mmio[OISP_ID];
	info->version = h->mmio[OISP_VERSION];
	info->width = h->mmio[0] & 0x1fff;		/* pending bank */
	info->height = (h->mmio[0] >> 16) & 0x1fff;
	info->frames_in = h->mmio[OISP_FRAMES_IN];
	info->frames_out = h->mmio[OISP_FRAMES_OUT];
	return 0;
}

static void mem_close(struct oisp_hal *h)
{
	munmap((void *)h->mmio, h->mmio_len);
	close(h->fd);
}

static const struct oisp_hal_ops mem_ops = { mem_write, mem_read, mem_stats, mem_info, mem_close };

/* ------------------------------------------------------------------ simulation */
static int sim_write(struct oisp_hal *h, const struct openisp_reg_write *w, int n, int commit)
{
	int i;

	for (i = 0; i < n; i++)
		fprintf(h->log, "W %x %x\n", w[i].addr, w[i].value);
	if (commit)
		fprintf(h->log, "C\n");
	fflush(h->log);
	return 0;
}

static int sim_read(struct oisp_hal *h, uint32_t addr, uint32_t *v)
{
	(void)h;
	*v = addr == OISP_ID ? OISP_ID_VALUE : 0;
	return 0;
}

static int sim_stats(struct oisp_hal *h, struct openisp_stats *st, uint32_t after, int timeout_ms)
{
	char path[512];
	FILE *f;
	size_t n;
	int waited = 0;

	snprintf(path, sizeof(path), "%s/stats_%u.bin", h->stats_dir, after + 1);
	/* the producer writes stats_<n>.bin atomically (rename); "end" stops */
	while (!(f = fopen(path, "rb"))) {
		char end[512];

		snprintf(end, sizeof(end), "%s/end", h->stats_dir);
		if (!access(end, F_OK))
			return -ENOENT;
		if (waited >= timeout_ms)
			return -ETIMEDOUT;
		usleep(1000);
		waited++;
	}
	n = fread(st, 1, sizeof(*st), f);
	fclose(f);
	if (n != sizeof(*st))
		return -EIO;
	st->frame = after + 1;
	return 0;
}

static int sim_info(struct oisp_hal *h, struct openisp_info *info)
{
	memset(info, 0, sizeof(*info));
	info->id = OISP_ID_VALUE;
	info->version = (OISP_VERSION_MAJOR << 24) | 3840;
	info->width = h->sim_width;
	info->height = h->sim_height;
	return 0;
}

static void sim_close(struct oisp_hal *h)
{
	fclose(h->log);
	free((void *)h->stats_dir);
}

static const struct oisp_hal_ops sim_ops = { sim_write, sim_read, sim_stats, sim_info, sim_close };

/* ------------------------------------------------------------------ open */
static int find_subdev(char *path, size_t len)
{
	DIR *d = opendir("/sys/class/video4linux");
	struct dirent *e;
	int found = -1;

	if (!d)
		return -1;
	while ((e = readdir(d)) && found < 0) {
		char p[512], name[128] = "";
		FILE *f;

		char drv[512];
		ssize_t n;

		if (strncmp(e->d_name, "v4l-subdev", 10))
			continue;
		/* bound to the openisp driver (entity name is the DT node, e.g. 80040000.isp) */
		snprintf(p, sizeof(p), "/sys/class/video4linux/%s/device/driver", e->d_name);
		n = readlink(p, drv, sizeof(drv) - 1);
		if (n > 0) {
			drv[n] = 0;
			if (!strcmp(strrchr(drv, '/') ? strrchr(drv, '/') + 1 : drv, "openisp")) {
				snprintf(path, len, "/dev/%s", e->d_name);
				found = 0;
				continue;
			}
		}
		snprintf(p, sizeof(p), "/sys/class/video4linux/%s/name", e->d_name);
		f = fopen(p, "r");
		if (!f)
			continue;
		if (fgets(name, sizeof(name), f) && strstr(name, "openisp")) {
			snprintf(path, len, "/dev/%s", e->d_name);
			found = 0;
		}
		fclose(f);
	}
	closedir(d);
	return found;
}

struct oisp_hal *oisp_hal_open(const char *spec, char *err, size_t errlen)
{
	struct oisp_hal *h = calloc(1, sizeof(*h));
	char path[512];

	if (!h) {
		snprintf(err, errlen, "out of memory");
		return NULL;
	}
	if (spec && !strncmp(spec, "sim:", 4)) {
		char *s = strdup(spec + 4), *dir, *size;

		dir = s ? strchr(s, ':') : NULL;
		size = dir ? strchr(dir + 1, ':') : NULL;
		if (!size || sscanf(size + 1, "%ux%u", &h->sim_width, &h->sim_height) != 2) {
			snprintf(err, errlen, "sim backend: sim:<log>:<stats dir>:<W>x<H>");
			free(s);
			free(h);
			return NULL;
		}
		*dir = *size = 0;
		h->log = fopen(s, "w");
		h->stats_dir = strdup(dir + 1);
		free(s);
		if (!h->log) {
			snprintf(err, errlen, "cannot create the write log");
			free((void *)h->stats_dir);
			free(h);
			return NULL;
		}
		h->ops = &sim_ops;
		return h;
	}
	if (spec && !strncmp(spec, "mem:", 4)) {
		unsigned long base = strtoul(spec + 4, NULL, 0);
		void *m;

		h->fd = open("/dev/mem", O_RDWR | O_SYNC);
		if (h->fd < 0) {
			snprintf(err, errlen, "/dev/mem: %s", strerror(errno));
			free(h);
			return NULL;
		}
		m = mmap(NULL, MMIO_SIZE, PROT_READ | PROT_WRITE, MAP_SHARED, h->fd, (off_t)base);
		if (m == MAP_FAILED) {
			snprintf(err, errlen, "mmap 0x%lx: %s", base, strerror(errno));
			close(h->fd);
			free(h);
			return NULL;
		}
		h->mmio = m;
		h->mmio_len = MMIO_SIZE;
		h->ops = &mem_ops;
		if (h->mmio[OISP_ID] != OISP_ID_VALUE) {
			snprintf(err, errlen, "no openISP at 0x%lx (ID 0x%08x)", base, h->mmio[OISP_ID]);
			mem_close(h);
			free(h);
			return NULL;
		}
		return h;
	}
	if (!spec) {
		if (find_subdev(path, sizeof(path))) {
			snprintf(err, errlen, "no openisp sub-device found (is openisp.ko loaded?)");
			free(h);
			return NULL;
		}
		spec = path;
	}
	h->fd = open(spec, O_RDWR);
	if (h->fd < 0) {
		snprintf(err, errlen, "%s: %s", spec, strerror(errno));
		free(h);
		return NULL;
	}
	h->ops = &drv_ops;
	return h;
}

void oisp_hal_close(struct oisp_hal *h)
{
	if (!h)
		return;
	h->ops->close(h);
	free(h);
}

int oisp_hal_apply(struct oisp_hal *h, const struct oisp_cfg *c, int with_tables, char *err, size_t errlen)
{
	struct openisp_reg_write w[OISP_GAMMA_SIZE + OISP_NLM_LUT_SIZE + OISP_NREG];
	struct oisp_cfg t = *c;
	uint32_t words[OISP_NREG];
	int n = 0, i, r;

	if (oisp_cfg_words(c, words, err, errlen))
		return -1;
	if (with_tables) {
		oisp_cfg_tables(&t);
		for (i = 0; i < OISP_GAMMA_SIZE; i++)
			w[n++] = (struct openisp_reg_write){ OISP_GAMMA_BASE + i, t.gamma_lut[i] };
		for (i = 0; i < OISP_NLM_LUT_SIZE; i++)
			w[n++] = (struct openisp_reg_write){ OISP_NLM_LUT_BASE + i, t.nlm_lut[i] };
	}
	for (i = 0; i < OISP_NREG; i++)
		w[n++] = (struct openisp_reg_write){ (uint32_t)i, words[i] };
	r = h->ops->write(h, w, n, 1);
	if (r) {
		snprintf(err, errlen, "register write failed: %s", strerror(-r));
		return -1;
	}
	return 0;
}
