#!/usr/bin/python
import numpy as np

class BCC:
    'Brightness Contrast Control'

    def __init__(self, img, brightness, contrast, clip):
        self.img = img
        self.brightness = brightness
        self.contrast = contrast
        self.clip = clip

    def clipping(self):
        np.clip(self.img, 0, self.clip, out=self.img)
        return self.img

    def execute(self):
        img_h = self.img.shape[0]
        img_w = self.img.shape[1]
        bcc_img = np.empty((img_h, img_w), np.int16)
        for y in range(img_h):
            for x in range(img_w):
                bcc_img[y,x] = self.img[y,x] + self.brightness
                bcc_img[y,x] = bcc_img[y,x] + (bcc_img[y,x] - 127) * self.contrast
        self.img = bcc_img
        return self.clipping()
