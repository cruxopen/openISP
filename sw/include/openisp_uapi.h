/* SPDX-License-Identifier: ((GPL-2.0 WITH Linux-syscall-note) OR MIT) */
/*
 * openISP - user space ABI of the openisp V4L2 sub-device driver.
 *
 * The 3A firmware (sw/) talks to the ISP through private ioctls on the
 * sub-device node (/dev/v4l-subdevN):
 *
 *   OPENISP_IOC_INFO       hardware id / version, active frame format
 *   OPENISP_IOC_PARAMS     a list of register writes, optionally committed
 *                          atomically at the next frame boundary
 *   OPENISP_IOC_STATS      wait for the 3A statistics of a newer frame
 *   OPENISP_IOC_READ_REG   debug: read one register word
 *
 * The ioctl numbers are in the V4L2 private range (BASE_VIDIOC_PRIVATE = 192).
 *
 * Addresses are ISP word addresses (sw/include/openisp_regs.h).
 */
#ifndef OPENISP_UAPI_H
#define OPENISP_UAPI_H

#ifdef __KERNEL__
#include <linux/types.h>
#include <linux/ioctl.h>
#else
#include <linux/types.h>
#include <sys/ioctl.h>
#endif

#define OPENISP_NZONE		256
#define OPENISP_HIST_BINS	256
#define OPENISP_MAX_WRITES	4096

struct openisp_info {
	__u32 id;		/* OISP_ID register, 0 if no hardware is mapped */
	__u32 version;		/* OISP_VERSION register */
	__u32 width;		/* sink pad format */
	__u32 height;
	__u32 mbus_code;	/* MEDIA_BUS_FMT_S????10_1X10 */
	__u32 frames_in;
	__u32 frames_out;
	__u32 reserved[9];
};

struct openisp_reg_write {
	__u32 addr;
	__u32 value;
};

#define OPENISP_PARAMS_COMMIT	(1u << 0)	/* commit at the next frame boundary */

struct openisp_params {
	__u32 count;		/* number of writes, <= OPENISP_MAX_WRITES */
	__u32 flags;		/* OPENISP_PARAMS_* */
	__u64 writes;		/* user pointer to struct openisp_reg_write[count] */
	__u32 frames_in;	/* out: FRAMES_IN when the commit was requested */
	__u32 reserved[3];
};

/* one frame of statistics, see hwmodel/stats.py for the definitions */
struct openisp_stats {
	__u32 frame;		/* in: last frame seen (wait for a newer one); out: frame number */
	__u32 timeout_ms;	/* in: 0 = do not wait */
	__u64 timestamp_ns;	/* CLOCK_MONOTONIC at end of frame */
	__u32 awb_r[OPENISP_NZONE];
	__u32 awb_g[OPENISP_NZONE];	/* Gr + Gb */
	__u32 awb_b[OPENISP_NZONE];
	__u32 awb_n[OPENISP_NZONE];
	__u32 ae_sum[OPENISP_NZONE];
	__u32 ae_hist[OPENISP_HIST_BINS];
	__u32 af[OPENISP_NZONE];
};

struct openisp_reg {
	__u32 addr;
	__u32 value;
};

#define OPENISP_IOC_INFO	_IOR('V', 0xc0, struct openisp_info)
#define OPENISP_IOC_PARAMS	_IOWR('V', 0xc1, struct openisp_params)
#define OPENISP_IOC_STATS	_IOWR('V', 0xc2, struct openisp_stats)
#define OPENISP_IOC_READ_REG	_IOWR('V', 0xc3, struct openisp_reg)

#endif /* OPENISP_UAPI_H */
