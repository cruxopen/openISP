#!/usr/bin/python
import numpy as np

class EE:
    'Edge Enhancement'

    def __init__(self, img, edge_filter, gain, thres, emclip):
        self.img = img
        self.edge_filter = edge_filter
        self.gain = gain
        self.thres = thres
        self.emclip = emclip

    def padding(self):
        img_pad = np.pad(self.img, ((1, 1), (2, 2)), 'reflect')
        return img_pad

    def clipping(self):
        np.clip(self.img, 0, 255, out=self.img)
        return self.img

    def emlut(self, val, thres, gain, clip):
        lut = 0
        if val < -thres[1]:
            lut = gain[1] * val
        elif val < -thres[0] and val > -thres[1]:
            lut = 0
        elif val < thres[0] and val > -thres[1]:
            lut = gain[0] * val
        elif val > thres[0] and val < thres[1]:
            lut = 0
        elif val > thres[1]:
            lut = gain[1] * val
        # np.clip(lut, clip[0], clip[1], out=lut)
        lut = max(clip[0], min(lut / 256, clip[1]))
        return lut

    def execute(self):
        img_pad = self.padding()
        img_h = self.img.shape[0]
        img_w = self.img.shape[1]
        ee_img = np.empty((img_h, img_w), np.int16)
        em_img = np.empty((img_h, img_w), np.int16)
        for y in range(img_pad.shape[0] - 2):
            for x in range(img_pad.shape[1] - 4):
                em_img[y,x] = np.sum(np.multiply(img_pad[y:y+3, x:x+5], self.edge_filter[:, :])) / 8
                ee_img[y,x] = img_pad[y+1,x+2] + self.emlut(em_img[y,x], self.thres, self.gain, self.emclip)
        self.img = ee_img
        return self.clipping(), em_img
