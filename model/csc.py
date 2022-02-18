#!/usr/bin/python
import numpy as np
from scipy.ndimage import correlate

class CSC:
    'Color Space Conversion'

    def __init__(self, img, csc):
        self.img = img
        self.csc = csc

    def execute(self):
        img_h = self.img.shape[0]
        img_w = self.img.shape[1]
        img_c = self.img.shape[2]
        csc_img = np.empty((img_h, img_w, img_c), np.uint32)
        # for y in range(img_h):
        #     for x in range(img_w):
        #         mulval = self.csc[:,0:3] * self.img[y,x,:]
        #         csc_img[y,x,0] = np.sum(mulval[0]) + self.csc[0,3]
        #         csc_img[y,x,1] = np.sum(mulval[1]) + self.csc[1,3]
        #         csc_img[y,x,2] = np.sum(mulval[2]) + self.csc[2,3]
        #         csc_img[y,x,:] = csc_img[y,x,:] / 1024

        csc_img[:, :, 0] = self.img[:, :, 0] * self.csc[0, 0] + self.img[:, :, 1] * self.csc[0, 1] + self.img[:, :, 2] * self.csc[0, 2] + self.csc[0, 3]
        csc_img[:, :, 1] = self.img[:, :, 0] * self.csc[1, 0] + self.img[:, :, 1] * self.csc[1, 1] + self.img[:, :, 2] * self.csc[1, 2] + self.csc[1, 3]
        csc_img[:, :, 2] = self.img[:, :, 0] * self.csc[2, 0] + self.img[:, :, 1] * self.csc[2, 1] + self.img[:, :, 2] * self.csc[2, 2] + self.csc[2, 3]
        csc_img = csc_img / 1024

        self.img = csc_img.astype(np.uint8)
        return self.img
