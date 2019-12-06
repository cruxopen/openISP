#!/usr/bin/python
import numpy as np

class AAF:
    'Anti-aliasing Filter'

    def __init__(self, img):
        self.img = img

    def padding(self):
        img_pad = np.pad(self.img, (2, 2), 'reflect')
        return img_pad

    def execute(self):
        img_pad = self.padding()
        raw_h = self.img.shape[0]
        raw_w = self.img.shape[1]
        aaf_img = np.empty((raw_h, raw_w), np.uint16)
        for y in range(img_pad.shape[0] - 4):
            for x in range(img_pad.shape[1] - 4):
                p0 = img_pad[y + 2, x + 2]
                p1 = img_pad[y, x]
                p2 = img_pad[y, x + 2]
                p3 = img_pad[y, x + 4]
                p4 = img_pad[y + 2, x]
                p5 = img_pad[y + 2, x + 4]
                p6 = img_pad[y + 4, x]
                p7 = img_pad[y + 4, x + 2]
                p8 = img_pad[y + 4, x + 4]
                aaf_img[y, x] = (p0 * 8 + p1 + p2 + p3 + p4 + p5 + p6 + p7 + p8) / 16
        self.img = aaf_img
        return self.img

