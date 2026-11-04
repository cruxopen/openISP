// SPDX-License-Identifier: GPL-2.0
/*
 * openISP hardware ISP - V4L2 sub-device for the Xilinx video pipeline.
 *
 * The sub-device sits between the MIPI CSI-2 RX and the frame buffer write
 * DMA in the xilinx-video composite graph:
 *
 *   sensor -> csiss -> [openisp: sink = Bayer RAW, source = output format] -> fb_wr
 *
 * Output formats (device tree "openisp,output-format"):
 *   "yuyv-raw" : M0 RAW capture. The hardware repacks every RAW10 pixel into
 *                a 16-bit YUYV8 carrier (Y = raw[7:0], C = raw[9:8]) so the
 *                stock frame buffer driver writes it losslessly. Memory layout
 *                equals V4L2_PIX_FMT_SRGGB10. Media bus code: UYVY8_1X16.
 *   "yuyv"     : processed output of the ISP (M3), packed 4:2:2 by isp_yuyv.
 *                Media bus code UYVY8_1X16 -> frame buffer YUYV.
 *   "nv12"     : processed output, 4:2:0. Media bus code VYYUYY8_1X24.
 *
 * With a "reg" resource (M3+, isp_axil AXI4-Lite port) the driver also
 *   - programs the frame size / Bayer order / statistics grid at stream on,
 *   - copies the 3A statistics of every frame in the FRAME_DONE interrupt,
 *   - exposes the private ioctls of openisp_uapi.h to the 3A firmware
 *     (sw/app/openisp.c): register writes committed at a frame boundary,
 *     blocking statistics read, hardware info.
 * Without "reg" (M0) it is a pure format-converting sub-device.
 */
#include <linux/clk.h>
#include <linux/delay.h>
#include <linux/interrupt.h>
#include <linux/io.h>
#include <linux/ktime.h>
#include <linux/mm.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/slab.h>
#include <linux/uaccess.h>
#include <linux/version.h>
#include <linux/wait.h>
#include <media/media-entity.h>
#include <media/v4l2-async.h>
#include <media/v4l2-subdev.h>

#include "openisp_regs.h"
#include "openisp_uapi.h"

#define OPENISP_PAD_SINK	0
#define OPENISP_PAD_SOURCE	1
#define OPENISP_NUM_PADS	2

#define OPENISP_MIN_W		64
#define OPENISP_MIN_H		64
#define OPENISP_MAX_W		4224
#define OPENISP_MAX_H		3136

#ifndef MEDIA_BUS_FMT_VYYUYY8_1X24
#define MEDIA_BUS_FMT_VYYUYY8_1X24	0x2100
#endif

static const struct {
	u32 code;
	u32 cfa;	/* cfa register: channel of pixel (y odd, x odd) at bits 2*(2y+x) */
} openisp_sink_codes[] = {
	{ MEDIA_BUS_FMT_SRGGB10_1X10, 0xe4 },	/* R  Gr / Gb B  -> 0,1,2,3 */
	{ MEDIA_BUS_FMT_SGRBG10_1X10, 0xb1 },	/* Gr R  / B  Gb -> 1,0,3,2 */
	{ MEDIA_BUS_FMT_SGBRG10_1X10, 0x4e },	/* Gb B  / R  Gr -> 2,3,0,1 */
	{ MEDIA_BUS_FMT_SBGGR10_1X10, 0x1b },	/* B  Gb / Gr R  -> 3,2,1,0 */
};

struct openisp {
	struct device *dev;
	struct v4l2_subdev sd;
	struct media_pad pads[OPENISP_NUM_PADS];
	struct v4l2_mbus_framefmt fmt[OPENISP_NUM_PADS];
	u32 src_code;
	u32 version;			/* OISP_VERSION: major.minor.maxw */
	struct mutex lock;		/* protects fmt, streaming, register sequences */

	void __iomem *base;		/* NULL: no register interface (M0) */
	size_t size;
	int irq;
	bool streaming;

	/* statistics: filled by the interrupt thread, read by OPENISP_IOC_STATS */
	struct mutex stats_lock;
	struct openisp_stats *stats;	/* last complete frame */
	struct openisp_stats *scratch;
	bool stats_valid;
	u32 stats_frame;		/* frame of *stats, for lock-free waiting */
	wait_queue_head_t stats_wq;
	u64 irq_count;
};

static inline struct openisp *to_openisp(struct v4l2_subdev *sd)
{
	return container_of(sd, struct openisp, sd);
}

static inline u32 isp_rd(struct openisp *isp, u32 word)
{
	return readl(isp->base + 4 * word);
}

static inline void isp_wr(struct openisp *isp, u32 word, u32 val)
{
	writel(val, isp->base + 4 * word);
}

static int openisp_sink_index(u32 code)
{
	int i;

	for (i = 0; i < ARRAY_SIZE(openisp_sink_codes); i++)
		if (openisp_sink_codes[i].code == code)
			return i;
	return -1;
}

/* ------------------------------------------------------------------ formats */
static struct v4l2_mbus_framefmt *
openisp_get_pad_fmt(struct openisp *isp, struct v4l2_subdev_state *sd_state,
		    unsigned int pad, u32 which)
{
	if (which == V4L2_SUBDEV_FORMAT_TRY)
#if LINUX_VERSION_CODE >= KERNEL_VERSION(6, 8, 0)
		return v4l2_subdev_state_get_format(sd_state, pad);
#else
		return v4l2_subdev_get_try_format(&isp->sd, sd_state, pad);
#endif
	return &isp->fmt[pad];
}

static void openisp_propagate(struct openisp *isp, const struct v4l2_mbus_framefmt *sink,
			      struct v4l2_mbus_framefmt *src)
{
	*src = *sink;
	src->code = isp->src_code;
}

static int openisp_enum_mbus_code(struct v4l2_subdev *sd,
				  struct v4l2_subdev_state *sd_state,
				  struct v4l2_subdev_mbus_code_enum *code)
{
	struct openisp *isp = to_openisp(sd);

	if (code->pad == OPENISP_PAD_SINK) {
		if (code->index >= ARRAY_SIZE(openisp_sink_codes))
			return -EINVAL;
		code->code = openisp_sink_codes[code->index].code;
		return 0;
	}
	if (code->index)
		return -EINVAL;
	code->code = isp->src_code;
	return 0;
}

static int openisp_get_fmt(struct v4l2_subdev *sd, struct v4l2_subdev_state *sd_state,
			   struct v4l2_subdev_format *fmt)
{
	struct openisp *isp = to_openisp(sd);

	mutex_lock(&isp->lock);
	fmt->format = *openisp_get_pad_fmt(isp, sd_state, fmt->pad, fmt->which);
	mutex_unlock(&isp->lock);
	return 0;
}

static int openisp_set_fmt(struct v4l2_subdev *sd, struct v4l2_subdev_state *sd_state,
			   struct v4l2_subdev_format *fmt)
{
	struct openisp *isp = to_openisp(sd);
	struct v4l2_mbus_framefmt *sink, *src;
	u32 maxw = OPENISP_MAX_W;

	mutex_lock(&isp->lock);
	if (fmt->which == V4L2_SUBDEV_FORMAT_ACTIVE && isp->streaming) {
		mutex_unlock(&isp->lock);
		return -EBUSY;
	}
	sink = openisp_get_pad_fmt(isp, sd_state, OPENISP_PAD_SINK, fmt->which);
	src = openisp_get_pad_fmt(isp, sd_state, OPENISP_PAD_SOURCE, fmt->which);

	if (fmt->pad == OPENISP_PAD_SOURCE) {
		/* source follows the sink, only the code is fixed by hardware */
		fmt->format = *src;
		mutex_unlock(&isp->lock);
		return 0;
	}

	if (isp->base)
		maxw = isp_rd(isp, OISP_VERSION) & 0xffff;	/* MAXW of the bitstream */
	if (openisp_sink_index(fmt->format.code) < 0)
		fmt->format.code = sink->code;
	fmt->format.width = clamp_t(u32, ALIGN(fmt->format.width, 2), OPENISP_MIN_W, maxw);
	fmt->format.height = clamp_t(u32, ALIGN(fmt->format.height, 2), OPENISP_MIN_H, OPENISP_MAX_H);
	fmt->format.field = V4L2_FIELD_NONE;
	if (fmt->format.colorspace == V4L2_COLORSPACE_DEFAULT)
		fmt->format.colorspace = V4L2_COLORSPACE_SRGB;

	*sink = fmt->format;
	openisp_propagate(isp, sink, src);
	mutex_unlock(&isp->lock);
	return 0;
}

/* ------------------------------------------------------------------ hardware */
/* write a register sequence, committed atomically at the next frame boundary */
static void openisp_write_commit(struct openisp *isp, const struct openisp_reg_write *w,
				 unsigned int n, bool commit)
{
	unsigned int i;

	if (commit)
		isp_wr(isp, OISP_CTRL, OISP_CTRL_HOLD);
	for (i = 0; i < n; i++)
		isp_wr(isp, w[i].addr, w[i].value);
	if (commit)
		isp_wr(isp, OISP_CTRL, OISP_CTRL_COMMIT);	/* also releases HOLD */
}

/* frame size, Bayer order and an automatic centred 16x16 statistics grid
 * (hwmodel.stats.zone_grid with zw = zh = 0) */
static void openisp_program_format(struct openisp *isp)
{
	const struct v4l2_mbus_framefmt *f = &isp->fmt[OPENISP_PAD_SINK];
	int idx = openisp_sink_index(f->code);
	u32 zw = (f->width / 16) & ~1u, zh = (f->height / 16) & ~1u;
	u32 x0 = ((f->width - 16 * zw) / 2) & ~1u, y0 = ((f->height - 16 * zh) / 2) & ~1u;
	struct openisp_reg_write w[] = {
		{ 0, f->width | (f->height << 16) },
		{ 1, openisp_sink_codes[idx < 0 ? 0 : idx].cfa },
		{ 53, x0 | (y0 << 16) },
		{ 54, zw | (zh << 16) },
	};

	openisp_write_commit(isp, w, ARRAY_SIZE(w), true);
}

static int openisp_s_stream(struct v4l2_subdev *sd, int enable)
{
	struct openisp *isp = to_openisp(sd);

	mutex_lock(&isp->lock);
	dev_dbg(isp->dev, "stream %s %ux%u\n", enable ? "on" : "off",
		isp->fmt[OPENISP_PAD_SINK].width, isp->fmt[OPENISP_PAD_SINK].height);
	if (isp->base) {
		if (enable) {
			/* flush a frame left over from an aborted stream (hardware 1.1+):
			 * it would keep FRAMES_IN != FRAMES_OUT and hold every commit */
			if ((isp->version & 0xffff0000) >= 0x01010000) {
				isp_wr(isp, OISP_CTRL, OISP_CTRL_RESET);
				udelay(1);
			}
			openisp_program_format(isp);
			isp_wr(isp, OISP_IRQ_STATUS, 0x7);
			if (isp->irq > 0)
				isp_wr(isp, OISP_IRQ_ENABLE, OISP_IRQ_FRAME_DONE);
		} else {
			isp_wr(isp, OISP_IRQ_ENABLE, 0);
		}
	}
	isp->streaming = enable;
	mutex_unlock(&isp->lock);
	return 0;
}

static void read_block(struct openisp *isp, u32 base, u32 *dst, int n)
{
	int i;

	for (i = 0; i < n; i++)
		dst[i] = isp_rd(isp, base + i);
}

/* copy one frame; false if the hardware published another frame meanwhile */
static bool openisp_copy_stats(struct openisp *isp, struct openisp_stats *st)
{
	u32 f0 = isp_rd(isp, OISP_ST_BAYER_FRAMES);

	read_block(isp, OISP_ST_AWB_R, st->awb_r, OPENISP_NZONE);
	read_block(isp, OISP_ST_AWB_G, st->awb_g, OPENISP_NZONE);
	read_block(isp, OISP_ST_AWB_B, st->awb_b, OPENISP_NZONE);
	read_block(isp, OISP_ST_AWB_N, st->awb_n, OPENISP_NZONE);
	read_block(isp, OISP_ST_AE_SUM, st->ae_sum, OPENISP_NZONE);
	read_block(isp, OISP_ST_AE_HIST, st->ae_hist, OPENISP_HIST_BINS);
	read_block(isp, OISP_ST_AF, st->af, OPENISP_NZONE);
	st->frame = f0;
	return isp_rd(isp, OISP_ST_BAYER_FRAMES) == f0 && isp_rd(isp, OISP_ST_AF_FRAMES) == f0;
}

static irqreturn_t openisp_irq(int irq, void *data)
{
	struct openisp *isp = data;
	u32 st = isp_rd(isp, OISP_IRQ_STATUS);

	if (!(st & isp_rd(isp, OISP_IRQ_ENABLE)))
		return IRQ_NONE;
	isp_wr(isp, OISP_IRQ_STATUS, st);
	isp->irq_count++;
	return (st & OISP_IRQ_FRAME_DONE) ? IRQ_WAKE_THREAD : IRQ_HANDLED;
}

static irqreturn_t openisp_irq_thread(int irq, void *data)
{
	struct openisp *isp = data;
	u64 ts = ktime_get_ns();
	int tries;

	/* ~1800 register reads; the statistics stay valid for one frame time */
	for (tries = 0; tries < 3; tries++)
		if (openisp_copy_stats(isp, isp->scratch))
			break;
	if (tries == 3) {
		dev_warn_ratelimited(isp->dev, "statistics changed while being read\n");
		return IRQ_HANDLED;
	}
	isp->scratch->timestamp_ns = ts;
	mutex_lock(&isp->stats_lock);
	if (!isp->stats_valid || isp->scratch->frame != isp->stats->frame) {
		swap(isp->stats, isp->scratch);
		WRITE_ONCE(isp->stats_frame, isp->stats->frame);
		smp_wmb();
		WRITE_ONCE(isp->stats_valid, true);
	}
	mutex_unlock(&isp->stats_lock);
	wake_up_interruptible(&isp->stats_wq);
	return IRQ_HANDLED;
}

/* ------------------------------------------------------------------ ioctls */
static bool openisp_addr_writable(u32 a)
{
	return a < OISP_NREG ||
	       (a >= OISP_GAMMA_BASE && a < OISP_GAMMA_BASE + OISP_GAMMA_SIZE) ||
	       (a >= OISP_NLM_LUT_BASE && a < OISP_NLM_LUT_BASE + OISP_NLM_LUT_SIZE);
}

static long openisp_ioc_params(struct openisp *isp, struct openisp_params *p)
{
	struct openisp_reg_write *w;
	unsigned int i;
	long ret = 0;

	if (!p->count || p->count > OPENISP_MAX_WRITES || p->flags & ~OPENISP_PARAMS_COMMIT)
		return -EINVAL;
	w = kvmalloc_array(p->count, sizeof(*w), GFP_KERNEL);
	if (!w)
		return -ENOMEM;
	if (copy_from_user(w, u64_to_user_ptr(p->writes), p->count * sizeof(*w))) {
		ret = -EFAULT;
		goto out;
	}
	for (i = 0; i < p->count; i++)
		if (!openisp_addr_writable(w[i].addr)) {
			ret = -EINVAL;
			goto out;
		}
	mutex_lock(&isp->lock);
	openisp_write_commit(isp, w, p->count, p->flags & OPENISP_PARAMS_COMMIT);
	p->frames_in = isp_rd(isp, OISP_FRAMES_IN);
	mutex_unlock(&isp->lock);
out:
	kvfree(w);
	return ret;
}

/* lock free: also evaluated by wait_event in TASK_INTERRUPTIBLE state */
static bool openisp_newer(struct openisp *isp, u32 frame)
{
	if (!READ_ONCE(isp->stats_valid))
		return false;
	smp_rmb();
	return READ_ONCE(isp->stats_frame) != frame;
}

static long openisp_ioc_stats(struct openisp *isp, struct openisp_stats *req)
{
	u32 last = req->frame;
	long t;

	if (isp->irq <= 0)
		return -ENODEV;
	if (!openisp_newer(isp, last)) {
		if (!req->timeout_ms)
			return -EAGAIN;
		t = wait_event_interruptible_timeout(isp->stats_wq, openisp_newer(isp, last),
						     msecs_to_jiffies(req->timeout_ms));
		if (t < 0)
			return t;
		if (!t)
			return -ETIMEDOUT;
	}
	mutex_lock(&isp->stats_lock);
	memcpy(req, isp->stats, sizeof(*req));
	mutex_unlock(&isp->stats_lock);
	return 0;
}

static long openisp_ioctl(struct v4l2_subdev *sd, unsigned int cmd, void *arg)
{
	struct openisp *isp = to_openisp(sd);

	switch (cmd) {
	case OPENISP_IOC_INFO: {
		struct openisp_info *i = arg;

		memset(i, 0, sizeof(*i));
		mutex_lock(&isp->lock);
		i->width = isp->fmt[OPENISP_PAD_SINK].width;
		i->height = isp->fmt[OPENISP_PAD_SINK].height;
		i->mbus_code = isp->fmt[OPENISP_PAD_SINK].code;
		mutex_unlock(&isp->lock);
		if (isp->base) {
			i->id = isp_rd(isp, OISP_ID);
			i->version = isp_rd(isp, OISP_VERSION);
			i->frames_in = isp_rd(isp, OISP_FRAMES_IN);
			i->frames_out = isp_rd(isp, OISP_FRAMES_OUT);
		}
		return 0;
	}
	case OPENISP_IOC_PARAMS:
		return isp->base ? openisp_ioc_params(isp, arg) : -ENODEV;
	case OPENISP_IOC_STATS:
		return isp->base ? openisp_ioc_stats(isp, arg) : -ENODEV;
	case OPENISP_IOC_READ_REG: {
		struct openisp_reg *r = arg;

		if (!isp->base)
			return -ENODEV;
		if (r->addr >= isp->size / 4)
			return -EINVAL;
		r->value = isp_rd(isp, r->addr);
		return 0;
	}
	default:
		return -ENOIOCTLCMD;
	}
}

static const struct v4l2_subdev_core_ops openisp_core_ops = {
	.ioctl = openisp_ioctl,
};

static const struct v4l2_subdev_video_ops openisp_video_ops = {
	.s_stream = openisp_s_stream,
};

static const struct v4l2_subdev_pad_ops openisp_pad_ops = {
	.enum_mbus_code = openisp_enum_mbus_code,
	.get_fmt = openisp_get_fmt,
	.set_fmt = openisp_set_fmt,
	.link_validate = v4l2_subdev_link_validate_default,
};

static const struct v4l2_subdev_ops openisp_ops = {
	.core = &openisp_core_ops,
	.video = &openisp_video_ops,
	.pad = &openisp_pad_ops,
};

static const struct media_entity_operations openisp_media_ops = {
	.link_validate = v4l2_subdev_link_validate,
};

/* ------------------------------------------------------------------ probe */
static void openisp_clk_disable(void *clk)
{
	clk_disable_unprepare(clk);
}

static int openisp_probe_hw(struct platform_device *pdev, struct openisp *isp)
{
	struct device *dev = &pdev->dev;
	struct resource *res;
	struct clk *clk;
	u32 id, ver;
	int ret;

	res = platform_get_resource(pdev, IORESOURCE_MEM, 0);
	if (!res)
		return 0;			/* M0: no register interface */

	clk = devm_clk_get_optional(dev, NULL);
	if (IS_ERR(clk))
		return dev_err_probe(dev, PTR_ERR(clk), "clock\n");
	ret = clk_prepare_enable(clk);
	if (ret)
		return ret;
	ret = devm_add_action_or_reset(dev, openisp_clk_disable, clk);
	if (ret)
		return ret;

	isp->base = devm_ioremap_resource(dev, res);
	if (IS_ERR(isp->base))
		return PTR_ERR(isp->base);
	isp->size = resource_size(res);
	id = isp_rd(isp, OISP_ID);
	ver = isp_rd(isp, OISP_VERSION);
	isp->version = ver;
	if (id != OISP_ID_VALUE)
		return dev_err_probe(dev, -ENODEV, "no openISP at %pR (id 0x%08x)\n", res, id);
	if (ver >> 24 != OISP_VERSION_MAJOR)
		return dev_err_probe(dev, -ENODEV, "unsupported version 0x%08x\n", ver);
	isp_wr(isp, OISP_IRQ_ENABLE, 0);
	isp_wr(isp, OISP_IRQ_STATUS, 0x7);

	isp->stats = devm_kzalloc(dev, sizeof(*isp->stats), GFP_KERNEL);
	isp->scratch = devm_kzalloc(dev, sizeof(*isp->scratch), GFP_KERNEL);
	if (!isp->stats || !isp->scratch)
		return -ENOMEM;

	isp->irq = platform_get_irq_optional(pdev, 0);
	if (isp->irq > 0) {
		ret = devm_request_threaded_irq(dev, isp->irq, openisp_irq, openisp_irq_thread,
						IRQF_ONESHOT, dev_name(dev), isp);
		if (ret)
			return dev_err_probe(dev, ret, "irq %d\n", isp->irq);
	} else {
		dev_warn(dev, "no interrupt: statistics ioctl disabled\n");
	}
	dev_info(dev, "openISP %u.%u, max width %u, irq %d\n", ver >> 24, (ver >> 16) & 0xff,
		 ver & 0xffff, isp->irq);
	return 0;
}

static int openisp_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct openisp *isp;
	const char *out = "nv12";
	int ret;

	isp = devm_kzalloc(dev, sizeof(*isp), GFP_KERNEL);
	if (!isp)
		return -ENOMEM;
	isp->dev = dev;
	mutex_init(&isp->lock);
	mutex_init(&isp->stats_lock);
	init_waitqueue_head(&isp->stats_wq);

	of_property_read_string(dev->of_node, "openisp,output-format", &out);
	if (!strcmp(out, "yuyv-raw") || !strcmp(out, "yuyv")) {
		isp->src_code = MEDIA_BUS_FMT_UYVY8_1X16;
	} else if (!strcmp(out, "nv12")) {
		isp->src_code = MEDIA_BUS_FMT_VYYUYY8_1X24;
	} else {
		dev_err(dev, "unsupported openisp,output-format \"%s\"\n", out);
		return -EINVAL;
	}

	ret = openisp_probe_hw(pdev, isp);
	if (ret)
		return ret;

	isp->fmt[OPENISP_PAD_SINK] = (struct v4l2_mbus_framefmt) {
		.width = 1920, .height = 1080,
		.code = MEDIA_BUS_FMT_SRGGB10_1X10,
		.field = V4L2_FIELD_NONE,
		.colorspace = V4L2_COLORSPACE_SRGB,
	};
	openisp_propagate(isp, &isp->fmt[OPENISP_PAD_SINK], &isp->fmt[OPENISP_PAD_SOURCE]);

	v4l2_subdev_init(&isp->sd, &openisp_ops);
	isp->sd.dev = dev;
	isp->sd.owner = THIS_MODULE;
	isp->sd.fwnode = of_fwnode_handle(dev->of_node);
	isp->sd.flags |= V4L2_SUBDEV_FL_HAS_DEVNODE;
	strscpy(isp->sd.name, dev_name(dev), sizeof(isp->sd.name));
	v4l2_set_subdevdata(&isp->sd, isp);

	isp->pads[OPENISP_PAD_SINK].flags = MEDIA_PAD_FL_SINK;
	isp->pads[OPENISP_PAD_SOURCE].flags = MEDIA_PAD_FL_SOURCE;
	isp->sd.entity.function = MEDIA_ENT_F_PROC_VIDEO_ISP;
	isp->sd.entity.ops = &openisp_media_ops;
	ret = media_entity_pads_init(&isp->sd.entity, OPENISP_NUM_PADS, isp->pads);
	if (ret)
		return ret;

	platform_set_drvdata(pdev, isp);
	ret = v4l2_async_register_subdev(&isp->sd);
	if (ret) {
		media_entity_cleanup(&isp->sd.entity);
		return ret;
	}
	dev_info(dev, "openISP sub-device registered, output %s%s\n", out,
		 isp->base ? "" : " (no register interface)");
	return 0;
}

static void openisp_remove_common(struct platform_device *pdev)
{
	struct openisp *isp = platform_get_drvdata(pdev);

	if (isp->base)
		isp_wr(isp, OISP_IRQ_ENABLE, 0);
	v4l2_async_unregister_subdev(&isp->sd);
	media_entity_cleanup(&isp->sd.entity);
	mutex_destroy(&isp->lock);
	mutex_destroy(&isp->stats_lock);
}

#if LINUX_VERSION_CODE >= KERNEL_VERSION(6, 11, 0)
static void openisp_remove(struct platform_device *pdev)
{
	openisp_remove_common(pdev);
}
#else
static int openisp_remove(struct platform_device *pdev)
{
	openisp_remove_common(pdev);
	return 0;
}
#endif

static const struct of_device_id openisp_of_match[] = {
	{ .compatible = "openisp,isp-1.0" },
	{ }
};
MODULE_DEVICE_TABLE(of, openisp_of_match);

static struct platform_driver openisp_driver = {
	.driver = {
		.name = "openisp",
		.of_match_table = openisp_of_match,
	},
	.probe = openisp_probe,
	.remove = openisp_remove,
};
module_platform_driver(openisp_driver);

MODULE_DESCRIPTION("openISP hardware ISP V4L2 sub-device");
MODULE_LICENSE("GPL v2");
