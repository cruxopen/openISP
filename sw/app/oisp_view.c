// SPDX-License-Identifier: MIT
/*
 * oisp_view - zero-copy HDMI/DP preview of a V4L2 capture node (KV260 M3).
 *
 * Every V4L2 capture buffer (frame buffer write DMA, YUYV, multi-planar API
 * with one plane as xilinx-video uses it) is exported as a
 * DMABUF and imported into DRM as a framebuffer; each new frame is put on a
 * display plane that scans YUYV directly (the ZynqMP DisplayPort video layer
 * converts YUV -> RGB in hardware).  The CPU never touches the pixels, so the
 * display follows the camera rate.  Only kernel UAPI headers are used (no
 * libdrm).  Needs DRM master: no X server / gdm on the display.
 *
 *   oisp_view [-d /dev/video0] [-c /dev/dri/card0] [-s /tmp/snap.yuv] [--list]
 *
 * -s FILE: when FILE.req exists, copy the next frame to FILE and remove
 * FILE.req (remote viewing; on request only: the capture buffers are
 * uncached, reading a 4K frame takes > 1 s and would stall the display).  --list: print the
 * DRM planes and their formats, then exit.  The 1920x1080 (sensor) format of
 * the media pipeline must already be set (capture_raw.sh does it).
 */
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>
#include <drm/drm.h>
#include <drm/drm_fourcc.h>
#include <drm/drm_mode.h>
#include <linux/videodev2.h>

#define NBUF 6

static volatile sig_atomic_t stop;
static void on_signal(int s) { (void)s; stop = 1; }

static void die(const char *what)
{
	fprintf(stderr, "oisp_view: %s: %s\n", what, strerror(errno));
	exit(1);
}

static int xioctl(int fd, unsigned long req, void *arg)
{
	int r;

	do
		r = ioctl(fd, req, arg);
	while (r < 0 && errno == EINTR);
	return r;
}

static double now(void)
{
	struct timespec t;

	clock_gettime(CLOCK_MONOTONIC, &t);
	return t.tv_sec + t.tv_nsec * 1e-9;
}

static void *zalloc(size_t n)
{
	void *p = calloc(n ? n : 1, 1);

	if (!p)
		die("calloc");
	return p;
}

/* ---------------------------------------------------------------- DRM */
struct plane {
	uint32_t id, possible_crtcs, nfmt, *fmt;
	int primary;
};

static int plane_type(int fd, uint32_t plane_id)
{
	struct drm_mode_obj_get_properties op = { .obj_id = plane_id, .obj_type = DRM_MODE_OBJECT_PLANE };
	uint32_t *ids;
	uint64_t *vals;
	int type = -1;

	if (xioctl(fd, DRM_IOCTL_MODE_OBJ_GETPROPERTIES, &op))
		return -1;
	ids = zalloc(op.count_props * sizeof(*ids));
	vals = zalloc(op.count_props * sizeof(*vals));
	op.props_ptr = (uintptr_t)ids;
	op.prop_values_ptr = (uintptr_t)vals;
	if (!xioctl(fd, DRM_IOCTL_MODE_OBJ_GETPROPERTIES, &op)) {
		for (uint32_t i = 0; i < op.count_props; i++) {
			struct drm_mode_get_property p = { .prop_id = ids[i] };

			if (!xioctl(fd, DRM_IOCTL_MODE_GETPROPERTY, &p) && !strcmp(p.name, "type"))
				type = (int)vals[i];
		}
	}
	free(ids);
	free(vals);
	return type;			/* DRM_PLANE_TYPE_OVERLAY 0, PRIMARY 1, CURSOR 2 */
}

/* find a property of an object by name; returns its id (0: none) and value */
static uint32_t find_prop(int fd, uint32_t obj, uint32_t type, const char *name, uint64_t *val, int print)
{
	struct drm_mode_obj_get_properties op = { .obj_id = obj, .obj_type = type };
	uint32_t *ids, found = 0;
	uint64_t *vals;

	if (xioctl(fd, DRM_IOCTL_MODE_OBJ_GETPROPERTIES, &op))
		return 0;
	ids = zalloc(op.count_props * sizeof(*ids));
	vals = zalloc(op.count_props * sizeof(*vals));
	op.props_ptr = (uintptr_t)ids;
	op.prop_values_ptr = (uintptr_t)vals;
	if (!xioctl(fd, DRM_IOCTL_MODE_OBJ_GETPROPERTIES, &op)) {
		for (uint32_t i = 0; i < op.count_props; i++) {
			struct drm_mode_get_property p = { .prop_id = ids[i] };

			if (xioctl(fd, DRM_IOCTL_MODE_GETPROPERTY, &p))
				continue;
			if (print)
				printf("    %s = %llu\n", p.name, (unsigned long long)vals[i]);
			if (name && !strcmp(p.name, name)) {
				found = ids[i];
				if (val)
					*val = vals[i];
			}
		}
	}
	free(ids);
	free(vals);
	return found;
}

static int set_prop(int fd, uint32_t obj, uint32_t type, const char *name, uint64_t v)
{
	struct drm_mode_obj_set_property sp = { .obj_id = obj, .obj_type = type };

	sp.prop_id = find_prop(fd, obj, type, name, NULL, 0);
	if (!sp.prop_id)
		return -1;
	sp.value = v;
	return xioctl(fd, DRM_IOCTL_MODE_OBJ_SETPROPERTY, &sp);
}

static int get_planes(int fd, struct plane **out)
{
	struct drm_mode_get_plane_res pr = { 0 };
	uint32_t *ids;
	struct plane *pl;

	if (xioctl(fd, DRM_IOCTL_MODE_GETPLANERESOURCES, &pr))
		die("GETPLANERESOURCES");
	ids = zalloc(pr.count_planes * sizeof(*ids));
	pr.plane_id_ptr = (uintptr_t)ids;
	if (xioctl(fd, DRM_IOCTL_MODE_GETPLANERESOURCES, &pr))
		die("GETPLANERESOURCES");
	pl = zalloc(pr.count_planes * sizeof(*pl));
	for (uint32_t i = 0; i < pr.count_planes; i++) {
		struct drm_mode_get_plane gp = { .plane_id = ids[i] };

		if (xioctl(fd, DRM_IOCTL_MODE_GETPLANE, &gp))
			die("GETPLANE");
		pl[i].fmt = zalloc(gp.count_format_types * sizeof(uint32_t));
		gp.format_type_ptr = (uintptr_t)pl[i].fmt;
		if (xioctl(fd, DRM_IOCTL_MODE_GETPLANE, &gp))
			die("GETPLANE");
		pl[i].id = ids[i];
		pl[i].possible_crtcs = gp.possible_crtcs;
		pl[i].nfmt = gp.count_format_types;
		pl[i].primary = plane_type(fd, ids[i]) == 1;
	}
	free(ids);
	*out = pl;
	return (int)pr.count_planes;
}

static int has_fmt(const struct plane *p, uint32_t f)
{
	for (uint32_t i = 0; i < p->nfmt; i++)
		if (p->fmt[i] == f)
			return 1;
	return 0;
}

struct output {
	uint32_t conn, crtc, crtc_idx;
	struct drm_mode_modeinfo mode;
};

static void find_output(int fd, struct output *o)
{
	struct drm_mode_card_res r = { 0 };
	uint32_t *conns, *crtcs, *encs;

	if (xioctl(fd, DRM_IOCTL_MODE_GETRESOURCES, &r))
		die("GETRESOURCES");
	conns = zalloc(r.count_connectors * 4);
	crtcs = zalloc(r.count_crtcs * 4);
	encs = zalloc(r.count_encoders * 4);
	r.connector_id_ptr = (uintptr_t)conns;
	r.crtc_id_ptr = (uintptr_t)crtcs;
	r.encoder_id_ptr = (uintptr_t)encs;
	r.count_fbs = 0;
	if (xioctl(fd, DRM_IOCTL_MODE_GETRESOURCES, &r))
		die("GETRESOURCES");
	for (uint32_t i = 0; i < r.count_connectors; i++) {
		struct drm_mode_get_connector c = { .connector_id = conns[i] };
		struct drm_mode_modeinfo *modes;
		uint32_t *cenc;

		if (xioctl(fd, DRM_IOCTL_MODE_GETCONNECTOR, &c))
			continue;
		if (c.connection != 1 || !c.count_modes)
			continue;
		modes = zalloc(c.count_modes * sizeof(*modes));
		cenc = zalloc(c.count_encoders * 4);
		c.modes_ptr = (uintptr_t)modes;
		c.encoders_ptr = (uintptr_t)cenc;
		c.count_props = 0;
		if (xioctl(fd, DRM_IOCTL_MODE_GETCONNECTOR, &c))
			die("GETCONNECTOR");
		o->conn = conns[i];
		o->mode = modes[0];
		for (uint32_t m = 0; m < c.count_modes; m++)
			if (modes[m].type & DRM_MODE_TYPE_PREFERRED) {
				o->mode = modes[m];
				break;
			}
		for (uint32_t e = 0; e < c.count_encoders; e++) {
			struct drm_mode_get_encoder ge = { .encoder_id = cenc[e] };

			if (xioctl(fd, DRM_IOCTL_MODE_GETENCODER, &ge))
				continue;
			for (uint32_t k = 0; k < r.count_crtcs; k++)
				if (ge.possible_crtcs & (1u << k)) {
					o->crtc = crtcs[k];
					o->crtc_idx = k;
					goto done;
				}
		}
done:
		free(modes);
		free(cenc);
		if (o->crtc)
			break;
	}
	free(conns);
	free(crtcs);
	free(encs);
	if (!o->crtc) {
		fprintf(stderr, "oisp_view: no connected display\n");
		exit(1);
	}
}

/* transparent black ARGB8888 dumb buffer for the primary plane (video on the overlay) */
static uint32_t black_fb(int fd, uint32_t w, uint32_t h)
{
	struct drm_mode_create_dumb cd = { .width = w, .height = h, .bpp = 32 };
	struct drm_mode_map_dumb md = { 0 };
	struct drm_mode_fb_cmd2 fb = { 0 };
	void *p;

	if (xioctl(fd, DRM_IOCTL_MODE_CREATE_DUMB, &cd))
		die("CREATE_DUMB");
	md.handle = cd.handle;
	if (xioctl(fd, DRM_IOCTL_MODE_MAP_DUMB, &md))
		die("MAP_DUMB");
	p = mmap(NULL, cd.size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, md.offset);
	if (p != MAP_FAILED) {
		memset(p, 0, cd.size);
		munmap(p, cd.size);
	}
	fb.width = w;
	fb.height = h;
	fb.pixel_format = DRM_FORMAT_ARGB8888;	/* ZynqMP graphics layer: no XRGB */
	fb.handles[0] = cd.handle;
	fb.pitches[0] = cd.pitch;
	if (xioctl(fd, DRM_IOCTL_MODE_ADDFB2, &fb))
		die("ADDFB2 (black)");
	return fb.fb_id;
}

static void set_crtc(int fd, const struct output *o, uint32_t fb)
{
	struct drm_mode_crtc c = { 0 };
	uint32_t conn = o->conn;

	c.crtc_id = o->crtc;
	c.fb_id = fb;
	c.set_connectors_ptr = (uintptr_t)&conn;
	c.count_connectors = 1;
	c.mode = o->mode;
	c.mode_valid = 1;
	if (xioctl(fd, DRM_IOCTL_MODE_SETCRTC, &c))
		die("SETCRTC");
}

static int set_plane(int fd, uint32_t plane, const struct output *o, uint32_t fb, uint32_t w, uint32_t h)
{
	struct drm_mode_set_plane sp = { 0 };
	uint32_t dw = o->mode.hdisplay, dh = o->mode.vdisplay;
	uint32_t cw = w < dw ? w : dw, ch = h < dh ? h : dh;

	/* 1:1, centred: the ZynqMP DisplayPort has no scaler, so a frame larger
	 * than the display (4K on a 1080p monitor) shows its centre crop */
	sp.plane_id = plane;
	sp.crtc_id = o->crtc;
	sp.fb_id = fb;
	sp.crtc_x = (int32_t)(dw - cw) / 2;
	sp.crtc_y = (int32_t)(dh - ch) / 2;
	sp.crtc_w = cw;
	sp.crtc_h = ch;
	/* ZynqMP DPDMA: every line must start 256-byte aligned -> crop x in
	 * steps of 128 YUYV pixels (the pitch is a multiple of 256 already) */
	sp.src_x = ((w - cw) / 2 & ~127u) << 16;
	sp.src_y = ((h - ch) / 2) << 16;
	sp.src_w = cw << 16;
	sp.src_h = ch << 16;
	return xioctl(fd, DRM_IOCTL_MODE_SETPLANE, &sp);
}

/* ---------------------------------------------------------------- main */
int main(int argc, char **argv)
{
	const char *vdev = "/dev/video0", *card = "/dev/dri/card0", *snap = NULL;
	int list = 0, vfd, dfd, np, vid = -1, prim = -1;
	struct plane *pl;
	struct output out = { 0 };
	struct v4l2_format f = { .type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE };
	struct v4l2_requestbuffers rb = { 0 };
	uint32_t fbs[NBUF], w, h;
	void *maps[NBUF];
	size_t lens[NBUF];
	int shown = -1, prev = -1;
	long nframes = 0, last_n = 0;
	double t0;
	char snap_req[512] = "";
	enum v4l2_buf_type type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE;	/* xilinx-video DMA */
	struct v4l2_plane planes[VIDEO_MAX_PLANES];
	uint32_t pitch;

	for (int i = 1; i < argc; i++) {
		if (!strcmp(argv[i], "-d") && i + 1 < argc)
			vdev = argv[++i];
		else if (!strcmp(argv[i], "-c") && i + 1 < argc)
			card = argv[++i];
		else if (!strcmp(argv[i], "-s") && i + 1 < argc)
			snap = argv[++i];
		else if (!strcmp(argv[i], "--list"))
			list = 1;
		else {
			fprintf(stderr, "usage: %s [-d video] [-c card] [-s snapshot.yuv] [--list]\n", argv[0]);
			return 1;
		}
	}

	dfd = open(card, O_RDWR | O_CLOEXEC);
	if (dfd < 0)
		die(card);
	{
		struct drm_set_client_cap cap = { DRM_CLIENT_CAP_UNIVERSAL_PLANES, 1 };

		xioctl(dfd, DRM_IOCTL_SET_CLIENT_CAP, &cap);
	}
	find_output(dfd, &out);
	np = get_planes(dfd, &pl);
	for (int i = 0; i < np; i++) {
		int usable = !!(pl[i].possible_crtcs & (1u << out.crtc_idx));

		if (list) {
			printf("plane %u %s crtcs 0x%x formats:", pl[i].id, pl[i].primary ? "primary" : "overlay",
			       pl[i].possible_crtcs);
			for (uint32_t k = 0; k < pl[i].nfmt; k++)
				printf(" %.4s", (const char *)&pl[i].fmt[k]);
			printf("\n");
			find_prop(dfd, pl[i].id, DRM_MODE_OBJECT_PLANE, NULL, NULL, 1);
		}
		if (usable && has_fmt(&pl[i], DRM_FORMAT_YUYV) && (vid < 0 || pl[i].primary))
			vid = i;
		if (usable && pl[i].primary)
			prim = i;
	}
	if (list) {
		printf("display %ux%u@%u, crtc %u, YUYV plane %d\n", out.mode.hdisplay, out.mode.vdisplay,
		       out.mode.vrefresh, out.crtc, vid >= 0 ? (int)pl[vid].id : -1);
		return 0;
	}
	if (vid < 0) {
		fprintf(stderr, "oisp_view: no display plane with YUYV\n");
		return 1;
	}
	if (xioctl(dfd, DRM_IOCTL_SET_MASTER, NULL))
		die("DRM master (is an X server / gdm running on the display?)");

	vfd = open(vdev, O_RDWR | O_CLOEXEC);
	if (vfd < 0)
		die(vdev);
	if (xioctl(vfd, VIDIOC_G_FMT, &f))
		die("G_FMT");
	f.fmt.pix_mp.pixelformat = V4L2_PIX_FMT_YUYV;
	f.fmt.pix_mp.num_planes = 1;
	if (xioctl(vfd, VIDIOC_S_FMT, &f))
		die("S_FMT YUYV");
	w = f.fmt.pix_mp.width;
	h = f.fmt.pix_mp.height;
	pitch = f.fmt.pix_mp.plane_fmt[0].bytesperline;
	rb.count = NBUF;
	rb.type = type;
	rb.memory = V4L2_MEMORY_MMAP;
	if (xioctl(vfd, VIDIOC_REQBUFS, &rb) || rb.count < 3)
		die("REQBUFS");
	for (uint32_t i = 0; i < rb.count; i++) {
		struct v4l2_buffer b = { .type = type, .memory = V4L2_MEMORY_MMAP, .index = i,
					 .m.planes = planes, .length = VIDEO_MAX_PLANES };
		struct v4l2_exportbuffer eb = { .type = type, .index = i, .plane = 0, .flags = O_RDONLY | O_CLOEXEC };
		struct drm_prime_handle ph = { 0 };
		struct drm_mode_fb_cmd2 fb = { 0 };

		if (xioctl(vfd, VIDIOC_QUERYBUF, &b))
			die("QUERYBUF");
		lens[i] = planes[0].length;
		maps[i] = mmap(NULL, planes[0].length, PROT_READ, MAP_SHARED, vfd, planes[0].m.mem_offset);
		if (xioctl(vfd, VIDIOC_EXPBUF, &eb))
			die("EXPBUF");
		ph.fd = eb.fd;
		if (xioctl(dfd, DRM_IOCTL_PRIME_FD_TO_HANDLE, &ph))
			die("PRIME_FD_TO_HANDLE");
		close(eb.fd);
		fb.width = w;
		fb.height = h;
		fb.pixel_format = DRM_FORMAT_YUYV;
		fb.handles[0] = ph.handle;
		fb.pitches[0] = pitch;
		if (xioctl(dfd, DRM_IOCTL_MODE_ADDFB2, &fb))
			die("ADDFB2 YUYV");
		fbs[i] = fb.fb_id;
		if (xioctl(vfd, VIDIOC_QBUF, &b))
			die("QBUF");
	}
	if (pl[vid].primary) {
		set_crtc(dfd, &out, 0);		/* mode only; the frames go on the primary plane */
	} else {
		if (prim < 0) {
			fprintf(stderr, "oisp_view: no primary plane\n");
			return 1;
		}
		set_crtc(dfd, &out, black_fb(dfd, out.mode.hdisplay, out.mode.vdisplay));
		/* ZynqMP DP: the graphics (primary) layer is blended over the video
		 * layer with a global alpha (default 255 = opaque): make it clear */
		if (set_prop(dfd, pl[prim].id, DRM_MODE_OBJECT_PLANE, "alpha", 0))
			fprintf(stderr, "oisp_view: cannot clear the primary plane alpha\n");
	}
	signal(SIGINT, on_signal);
	signal(SIGTERM, on_signal);
	if (xioctl(vfd, VIDIOC_STREAMON, &type))
		die("STREAMON");
	fprintf(stderr, "oisp_view: %ux%u YUYV -> %s plane %u, display %ux%u@%u\n", w, h,
		pl[vid].primary ? "primary" : "overlay", pl[vid].id, out.mode.hdisplay, out.mode.vdisplay,
		out.mode.vrefresh);
	t0 = now();
	if (snap)
		snprintf(snap_req, sizeof(snap_req), "%s.req", snap);
	while (!stop) {
		struct v4l2_buffer b = { .type = type, .memory = V4L2_MEMORY_MMAP,
					 .m.planes = planes, .length = VIDEO_MAX_PLANES };
		double t;

		if (xioctl(vfd, VIDIOC_DQBUF, &b)) {
			if (errno == EAGAIN)
				continue;
			die("DQBUF");
		}
		if (set_plane(dfd, pl[vid].id, &out, fbs[b.index], w, h))
			die("SETPLANE");
		/* the buffer shown two frames ago is no longer scanned out */
		if (prev >= 0) {
			struct v4l2_plane qp[VIDEO_MAX_PLANES];
			struct v4l2_buffer q = { .type = type, .memory = V4L2_MEMORY_MMAP, .index = (uint32_t)prev,
						 .m.planes = qp, .length = VIDEO_MAX_PLANES };

			memset(qp, 0, sizeof(qp));

			if (xioctl(vfd, VIDIOC_QBUF, &q))
				die("QBUF");
		}
		prev = shown;
		shown = (int)b.index;
		nframes++;
		t = now();
		if (snap && maps[b.index] != MAP_FAILED && !access(snap_req, F_OK)) {
			char tmp[520];
			FILE *s;

			snprintf(tmp, sizeof(tmp), "%s.tmp", snap);
			s = fopen(tmp, "wb");
			if (s) {
				fwrite(maps[b.index], 1, (size_t)pitch * h, s);
				fclose(s);
				rename(tmp, snap);
			}
			unlink(snap_req);
		}
		if (t - t0 >= 5.0) {
			fprintf(stderr, "oisp_view: %.2f fps\n", (nframes - last_n) / (t - t0));
			last_n = nframes;
			t0 = t;
		}
	}
	xioctl(vfd, VIDIOC_STREAMOFF, &type);
	if (!pl[vid].primary)		/* leave the graphics layer opaque for the desktop */
		set_prop(dfd, pl[prim].id, DRM_MODE_OBJECT_PLANE, "alpha", 255);
	for (uint32_t i = 0; i < rb.count; i++)
		if (maps[i] != MAP_FAILED)
			munmap(maps[i], lens[i]);
	(void)lens;
	return 0;
}
