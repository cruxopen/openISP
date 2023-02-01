#!/usr/bin/python
import numpy as np

class DPC:
    'Dead Pixel Correction'

    def __init__(self, img, thres, mode, clip):
        self.img = img
        self.thres = thres
        self.mode = mode
        self.clip = clip

    def padding(self):
        img_pad = np.pad(self.img, (2, 2), 'reflect')
        return img_pad

    def clipping(self):
        np.clip(self.img, 0, self.clip, out=self.img)
        return self.img

    def execute(self):

        """
        Pixel array in code is showed above:

        p1 p2 p3
        p4 p0 p5
        p6 p7 p8

        it makes sense for calculating follow-up gradients of pixel values (horizontal,vertical,left/right diagonal).
        """


        img_pad = self.padding()
        raw_h = self.img.shape[0]
        raw_w = self.img.shape[1]
        dpc_img = np.empty((raw_h, raw_w), np.uint16) 
        # change uint16 to int_, still exists overflow warning  in the following abs calculation
        for y in range(img_pad.shape[0] - 4):
            for x in range(img_pad.shape[1] - 4):



                p0 = img_pad[y + 2, x + 2].astype(int)
                p1 = img_pad[y, x].astype(int)
                p2 = img_pad[y, x + 2].astype(int)
                p3 = img_pad[y, x + 4].astype(int)
                p4 = img_pad[y + 2, x].astype(int)
                p5 = img_pad[y + 2, x + 4].astype(int)
                p6 = img_pad[y + 4, x].astype(int)
                p7 = img_pad[y + 4, x + 2].astype(int)
                p8 = img_pad[y + 4, x + 4].astype(int)



                if (abs(p1 - p0) > self.thres) and (abs(p2 - p0) > self.thres) and (abs(p3 - p0) > self.thres) \
                        and (abs(p4 - p0) > self.thres) and (abs(p5 - p0) > self.thres) and (abs(p6 - p0) > self.thres) \
                        and (abs(p7 - p0) > self.thres) and (abs(p8 - p0) > self.thres):
                    if self.mode == 'mean':
                        p0 = (p2 + p4 + p5 + p7) / 4
                    elif self.mode == 'gradient':
                        dv = abs(2 * p0 - p2 - p7)
                        dh = abs(2 * p0 - p4 - p5)
                        ddl = abs(2 * p0 - p1 - p8)
                        ddr = abs(2 * p0 - p3 - p6)
                        if (min(dv, dh, ddl, ddr) == dv):
                            p0 = (p2 + p7 + 1) / 2
                        elif (min(dv, dh, ddl, ddr) == dh):
                            p0 = (p4 + p5 + 1) / 2
                        elif (min(dv, dh, ddl, ddr) == ddl):
                            p0 = (p1 + p8 + 1) / 2
                        else:
                            p0 = (p3 + p6 + 1) / 2
                dpc_img[y, x] = p0.astype('uint16')
        self.img = dpc_img
        return self.clipping()

