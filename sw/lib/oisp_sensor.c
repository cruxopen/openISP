// SPDX-License-Identifier: MIT
/*
 * Sensor exposure / analog gain / focus through V4L2 controls on the sensor
 * sub-device.  Exposure (lines): the control named by sensor.exposure_ctrl in
 * the tuning file, else V4L2_CID_EXPOSURE, else a control whose name contains
 * "integration_time" or "exposure" (the KV260 AR1335 driver has a private
 * "coarse_integration_time").  Gain: sensor.gain_ctrl, else
 * V4L2_CID_ANALOGUE_GAIN, else V4L2_CID_GAIN.  Focus: V4L2_CID_FOCUS_ABSOLUTE.
 * The gain code -> gain mapping is ae.gain_table (an index control) or
 * ae.gain_1x (code = gain x gain_1x).
 */
#include <ctype.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>
#include <linux/videodev2.h>

#include "oisp.h"

static int query(int fd, uint32_t id, int *min, int *max)
{
	struct v4l2_queryctrl q;

	memset(&q, 0, sizeof(q));
	q.id = id;
	if (ioctl(fd, VIDIOC_QUERYCTRL, &q) < 0 || (q.flags & V4L2_CTRL_FLAG_DISABLED))
		return 0;
	*min = q.minimum;
	*max = q.maximum;
	return 1;
}

static int find_by_name(const char *name, char *path, size_t len)
{
	DIR *d = opendir("/sys/class/video4linux");
	struct dirent *e;
	int r = -1;

	if (!d)
		return -1;
	while (r && (e = readdir(d))) {
		char p[512], n[128] = "";
		FILE *f;

		if (strncmp(e->d_name, "v4l-subdev", 10))
			continue;
		snprintf(p, sizeof(p), "/sys/class/video4linux/%s/name", e->d_name);
		f = fopen(p, "r");
		if (!f)
			continue;
		if (fgets(n, sizeof(n), f) && strstr(n, name)) {
			snprintf(path, len, "/dev/%s", e->d_name);
			r = 0;
		}
		fclose(f);
	}
	closedir(d);
	return r;
}

static int name_eq(const char *a, const char *b)
{
	for (; *a && *b; a++, b++) {
		char x = tolower((unsigned char)*a), y = tolower((unsigned char)*b);

		if ((x == ' ' ? '_' : x) != (y == ' ' ? '_' : y))
			return 0;
	}
	return !*a && !*b;
}

/* integer control by exact name (case and '_' / ' ' insensitive), or the
 * first whose name contains sub (when name is NULL) */
static int query_name(int fd, const char *name, const char *sub, uint32_t *id, int *min, int *max,
		      char *found, size_t flen)
{
	struct v4l2_queryctrl q;

	memset(&q, 0, sizeof(q));
	q.id = V4L2_CTRL_FLAG_NEXT_CTRL;
	while (ioctl(fd, VIDIOC_QUERYCTRL, &q) == 0) {
		char n[sizeof(q.name) + 1], *c;

		snprintf(n, sizeof(n), "%s", (const char *)q.name);
		for (c = n; *c; c++)
			*c = *c == ' ' ? '_' : tolower((unsigned char)*c);
		if (q.type == V4L2_CTRL_TYPE_INTEGER && !(q.flags & V4L2_CTRL_FLAG_DISABLED) &&
		    (name ? name_eq(n, name) : strstr(n, sub) != NULL)) {
			*id = q.id;
			*min = q.minimum;
			*max = q.maximum;
			size_t k;

			for (k = 0; k + 1 < flen && n[k]; k++)
				found[k] = n[k];
			found[k] = 0;
			return 1;
		}
		q.id |= V4L2_CTRL_FLAG_NEXT_CTRL;
	}
	return 0;
}

static int find_ctrl(int fd, const char *name, const uint32_t *cids, const char *const *subs,
		     uint32_t *id, int *min, int *max, char *found, size_t flen)
{
	if (name && name[0])
		return query_name(fd, name, NULL, id, min, max, found, flen);
	for (; *cids; cids++)
		if (query(fd, *cids, min, max)) {
			struct v4l2_queryctrl q;

			memset(&q, 0, sizeof(q));
			q.id = *id = *cids;
			ioctl(fd, VIDIOC_QUERYCTRL, &q);
			snprintf(found, flen, "%s", (const char *)q.name);
			return 1;
		}
	for (; subs && *subs; subs++)
		if (query_name(fd, NULL, *subs, id, min, max, found, flen))
			return 1;
	return 0;
}

int oisp_sensor_open(struct oisp_sensor *s, const char *name, const char *exposure_ctrl,
		     const char *gain_ctrl, char *err, size_t errlen)
{
	static const uint32_t exp_cids[] = { V4L2_CID_EXPOSURE, 0 };
	static const uint32_t gain_cids[] = { V4L2_CID_ANALOGUE_GAIN, V4L2_CID_GAIN, 0 };
	static const char *const exp_subs[] = { "integration_time", "exposure", NULL };

	char path[512];

	memset(s, 0, sizeof(*s));
	s->fd = -1;
	if (name[0] == '/')
		snprintf(path, sizeof(path), "%s", name);
	else if (find_by_name(name, path, sizeof(path))) {
		snprintf(err, errlen, "no sensor sub-device named \"%s\"", name);
		return -1;
	}
	s->fd = open(path, O_RDWR);
	if (s->fd < 0) {
		snprintf(err, errlen, "%s: %s", path, strerror(errno));
		return -1;
	}
	s->has_exposure = find_ctrl(s->fd, exposure_ctrl, exp_cids, exp_subs, &s->exp_cid,
				    &s->exp_min, &s->exp_max, s->exp_name, sizeof(s->exp_name));
	s->has_gain = find_ctrl(s->fd, gain_ctrl, gain_cids, NULL, &s->gain_cid,
				&s->gain_min, &s->gain_max, s->gain_name, sizeof(s->gain_name));
	if ((exposure_ctrl && exposure_ctrl[0] && !s->has_exposure) ||
	    (gain_ctrl && gain_ctrl[0] && !s->has_gain)) {
		snprintf(err, errlen, "%s: no control \"%s\"", path,
			 !s->has_exposure && exposure_ctrl && exposure_ctrl[0] ? exposure_ctrl : gain_ctrl);
		close(s->fd);
		s->fd = -1;
		return -1;
	}
	s->has_focus = query(s->fd, V4L2_CID_FOCUS_ABSOLUTE, &s->focus_min, &s->focus_max);
	return 0;
}

int oisp_sensor_set(struct oisp_sensor *s, int lines, int gain_code)
{
	struct v4l2_ext_control c[2];
	struct v4l2_ext_controls cs;
	int n = 0;

	memset(c, 0, sizeof(c));
	if (s->fd < 0)
		return -ENODEV;
	if (s->has_exposure) {
		c[n].id = s->exp_cid;
		c[n++].value = lines < s->exp_min ? s->exp_min : lines > s->exp_max ? s->exp_max : lines;
	}
	if (s->has_gain) {
		c[n].id = s->gain_cid;
		c[n++].value = gain_code < s->gain_min ? s->gain_min :
			       gain_code > s->gain_max ? s->gain_max : gain_code;
	}
	if (!n)
		return 0;
	memset(&cs, 0, sizeof(cs));
	cs.which = V4L2_CTRL_WHICH_CUR_VAL;
	cs.count = n;
	cs.controls = c;
	return ioctl(s->fd, VIDIOC_S_EXT_CTRLS, &cs) < 0 ? -errno : 0;
}

int oisp_sensor_focus(struct oisp_sensor *s, int pos)
{
	struct v4l2_control c = { V4L2_CID_FOCUS_ABSOLUTE, pos };

	if (s->fd < 0 || !s->has_focus)
		return -ENODEV;
	return ioctl(s->fd, VIDIOC_S_CTRL, &c) < 0 ? -errno : 0;
}

void oisp_sensor_close(struct oisp_sensor *s)
{
	if (s->fd >= 0)
		close(s->fd);
	s->fd = -1;
}
