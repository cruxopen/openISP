#!/usr/bin/python
import numpy as np

class BLC:
    'Black Level Compensation'

    def __init__(self, img, parameter, bayer_pattern, clip):
        self.img = img
        self.parameter = parameter
        self.bayer_pattern = bayer_pattern
        self.clip = clip

    def clipping(self):
        np.clip(self.img, 0, self.clip, out=self.img)
        return self.img

    def execute(self):
        bl_r = self.parameter[0]
        bl_gr = self.parameter[1]
        bl_gb = self.parameter[2]
        bl_b = self.parameter[3]
        alpha = self.parameter[4]
        beta = self.parameter[5]
        raw_h = self.img.shape[0]
        raw_w = self.img.shape[1]
        blc_img = np.empty((raw_h,raw_w), np.int16)
        if self.bayer_pattern == 'rggb':
            r = self.img[::2, ::2] + bl_r
            b = self.img[1::2, 1::2] + bl_b
            gr = self.img[::2, 1::2] + bl_gr + alpha * r / 256
            gb = self.img[1::2, ::2] + bl_gb + beta * b / 256
            blc_img[::2, ::2] = r
            blc_img[::2, 1::2] = gr
            blc_img[1::2, ::2] = gb
            blc_img[1::2, 1::2] = b
        elif self.bayer_pattern == 'bggr':
            b = self.img[::2, ::2] + bl_b
            r = self.img[1::2, 1::2] + bl_r
            gb = self.img[::2, 1::2] + bl_gb + beta * b / 256
            gr = self.img[1::2, ::2] + bl_gr + alpha * r / 256
            blc_img[::2, ::2] = b
            blc_img[::2, 1::2] = gb
            blc_img[1::2, ::2] = gr
            blc_img[1::2, 1::2] = r
        elif self.bayer_pattern == 'gbrg':
            b = self.img[::2, 1::2] + bl_b
            r = self.img[1::2, ::2] + bl_r
            gb = self.img[::2, ::2] + bl_gb + beta * b / 256
            gr = self.img[1::2, 1::2] + bl_gr + alpha * r / 256
            blc_img[::2, ::2] = gb
            blc_img[::2, 1::2] = b
            blc_img[1::2, ::2] = r
            blc_img[1::2, 1::2] = gr
        elif self.bayer_pattern == 'grbg':
            r = self.img[::2, 1::2] + bl_r
            b = self.img[1::2, ::2] + bl_b
            gr = self.img[::2, ::2] + bl_gr + alpha * r / 256
            gb = self.img[1::2, 1::2] + bl_gb + beta * b / 256
            blc_img[::2, ::2] = gr
            blc_img[::2, 1::2] = r
            blc_img[1::2, ::2] = b
            blc_img[1::2, 1::2] = gb
        self.img = blc_img
        return self.clipping()

