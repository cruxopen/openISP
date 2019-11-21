#!/usr/bin/python
import numpy as np

class FCS:
    'False Color Suppresion'

    def __init__(self, img, edgemap, fcs_edge, gain, intercept, slope):
        self.img = img
        self.edgemap = edgemap
        self.fcs_edge = fcs_edge
        self.gain = gain
        self.intercept = intercept
        self.slope = slope

    def clipping(self):
        np.clip(self.img, 0, 255, out=self.img)
        return self.img

    def execute(self):
        img_h = self.img.shape[0]
        img_w = self.img.shape[1]
        img_c = self.img.shape[2]
        fcs_img = np.empty((img_h, img_w, img_c), np.int16)
        for y in range(img_h):
            for x in range(img_w):
                if np.abs(self.edgemap[y,x]) <= self.fcs_edge[0]:
                    uvgain = self.gain
                elif np.abs(self.edgemap[y,x]) > self.fcs_edge[0] and np.abs(self.edgemap[y,x]) < self.fcs_edge[1]:
                    uvgain = self.intercept - self.slope * self.edgemap[y,x]
                else:
                    uvgain = 0
                fcs_img[y,x,:] = uvgain * (self.img[y,x,:]) / 256 + 128
        self.img = fcs_img
        return self.clipping()
