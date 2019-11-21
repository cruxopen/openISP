#!/usr/bin/python
import numpy as np

class GC:
    'Gamma Correction'

    def __init__(self, img, lut, mode):
        self.img = img
        self.lut = lut
        self.mode = mode

    def execute(self):
        img_h = self.img.shape[0]
        img_w = self.img.shape[1]
        img_c = self.img.shape[2]
        gc_img = np.empty((img_h, img_w, img_c), np.uint16)
        for y in range(self.img.shape[0]):
            for x in range(self.img.shape[1]):
                if self.mode == 'rgb':
                    gc_img[y, x, 0] = self.lut[self.img[y, x, 0]]
                    gc_img[y, x, 1] = self.lut[self.img[y, x, 1]]
                    gc_img[y, x, 2] = self.lut[self.img[y, x, 2]]
                    gc_img[y, x, :] = gc_img[y, x, :] / 4
                elif self.mode == 'yuv':
                    gc_img[y, x, 0] = self.lut[0][self.img[y, x, 0]]
                    gc_img[y, x, 1] = self.lut[1][self.img[y, x, 1]]
                    gc_img[y, x, 2] = self.lut[1][self.img[y, x, 2]]
        self.img = gc_img
        return self.img
