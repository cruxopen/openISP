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
        for y in range(0, raw_h-1, 2):
            for x in range(0, raw_w-1, 2):
                if self.bayer_pattern == 'rggb':
                    r = self.img[y,x] + bl_r
                    b = self.img[y+1,x+1] + bl_b
                    gr = self.img[y,x+1] + bl_gr + alpha * r / 256
                    gb = self.img[y+1,x] + bl_gb + beta * b / 256
                    blc_img[y,x] = r
                    blc_img[y,x+1] = gr
                    blc_img[y+1,x] = gb
                    blc_img[y+1,x+1] = b
                elif self.bayer_pattern == 'bggr':
                    b = self.img[y,x] + bl_b
                    r = self.img[y+1,x+1] + bl_r
                    gb = self.img[y,x+1] + bl_gb + beta * b / 256
                    gr = self.img[y+1,x] + bl_gr + alpha * r / 256
                    blc_img[y,x] = b
                    blc_img[y,x+1] = gb
                    blc_img[y+1,x] = gr
                    blc_img[y+1,x+1] = r
                elif self.bayer_pattern == 'gbrg':
                    b = self.img[y,x+1] + bl_b
                    r = self.img[y+1,x] + bl_r
                    gb = self.img[y,x] + bl_gb + beta * b / 256
                    gr = self.img[y+1,x+1] + bl_gr + alpha * r / 256
                    blc_img[y,x] = gb
                    blc_img[y,x+1] = b
                    blc_img[y+1,x] = r
                    blc_img[y+1,x+1] = gr
                elif self.bayer_pattern == 'grbg':
                    r = self.img[y,x+1] + bl_r
                    b = self.img[y+1,x] + bl_b
                    gr = self.img[y,x] + bl_gr + alpha * r / 256
                    gb = self.img[y+1,x+1] + bl_gb + beta * b / 256
                    blc_img[y,x] = gr
                    blc_img[y,x+1] = r
                    blc_img[y+1,x] = b
                    blc_img[y+1,x+1] = gb
        self.img = blc_img
        return self.clipping()

