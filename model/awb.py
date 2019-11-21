#!/usr/bin/python
import numpy as np

class WBGC:
    'Auto White Balance Gain Control'

    def __init__(self, img, parameter, bayer_pattern, clip):
        self.img = img
        self.parameter = parameter
        self.bayer_pattern = bayer_pattern
        self.clip = clip

    def clipping(self):
        np.clip(self.img, 0, self.clip, out=self.img)
        return self.img

    def execute(self):
        r_gain = self.parameter[0]
        gr_gain = self.parameter[1]
        gb_gain = self.parameter[2]
        b_gain = self.parameter[3]
        raw_h = self.img.shape[0]
        raw_w = self.img.shape[1]
        awb_img = np.empty((raw_h, raw_w), np.int16)
        for y in range(0, raw_h-1, 2):
            for x in range(0, raw_w-1, 2):
                if self.bayer_pattern == 'rggb':
                    r = self.img[y,x] * r_gain
                    gr = self.img[y,x+1] * gr_gain
                    gb = self.img[y+1,x] * gb_gain
                    b = self.img[y+1,x+1] * b_gain
                    awb_img[y,x] = r
                    awb_img[y,x+1] = gr
                    awb_img[y+1,x] = gb
                    awb_img[y+1,x+1] = b
                elif self.bayer_pattern == 'bggr':
                    b = self.img[y,x] * b_gain
                    gb = self.img[y,x+1] * gb_gain
                    gr = self.img[y+1,x] * gr_gain
                    r = self.img[y+1,x+1] * r_gain
                    awb_img[y,x] = b
                    awb_img[y,x+1] = gb
                    awb_img[y+1,x] = gr
                    awb_img[y+1,x+1] = r
                elif self.bayer_pattern == 'gbrg':
                    gb = self.img[y,x] * gb_gain
                    b = self.img[y,x+1] * b_gain
                    r = self.img[y+1,x] * r_gain
                    gr = self.img[y+1,x+1] * gr_gain
                    awb_img[y,x] = gb
                    awb_img[y,x+1] = b
                    awb_img[y+1,x] = r
                    awb_img[y+1,x+1] = gr
                elif self.bayer_pattern == 'grbg':
                    gr = self.img[y,x] * gr_gain
                    r = self.img[y,x+1] * r_gain
                    b = self.img[y+1,x] * b_gain
                    gb = self.img[y+1,x+1] * gb_gain
                    awb_img[y,x] = gr
                    awb_img[y,x+1] = r
                    awb_img[y+1,x] = b
                    awb_img[y+1,x+1] = gb
        self.img = awb_img
        return self.clipping()
