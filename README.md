# Open Image Signal Processor (openISP)

## Introduction

Image Signal Processor (ISP) is an application processor to do digital image processing, specifically for conversion from RAW image (acquired from Imaging Sensors) to RGB/YUV image (to further processing or display).

![](https://github.com/cruxopen/openISP/blob/master/images/Image%20Signal%20Processor.png)
## Objectives

This project aims to provide an overview of ISP and stimulate the whole ISP pipeline and some tuning functions from hardware perspectives. The proposed ISP pipeline consists of following modules, dead pixel correction (DPC), black level compensation (BLC), lens shading correction (LSC), anti-aliasing noise filter (ANF), auto white balance gain control (AWB), color filter array interpolation (CFA), gamma correction (GC), color correction matrix (CCM), color space conversion (CSC), noise filter for luma and chroma (NF), edge enhancement (EE), false color suppression (FCS), hue/saturation/control (HSC) and brightness/contast control (BCC). The ISP pipeline architecture refers from [1], directly captured from book.

![ISP Pipeline](https://github.com/cruxopen/openISP/blob/master/images/isp_pipeline.png)

Some advanced functions like wide/high dynamic range (W/HDR) and temporal/spatial noise filter (T/SNF) will be implemented in the future.

The new ISP pipeline is modified based on previous one to make the pipeline more reasonable and the ISP performance better.

![ISP Pipeline2](https://github.com/cruxopen/openISP/blob/master/images/isp_pipeline2.png)

- [x] Dead Pixel Correction
- [x] Black Level Compensation
- [ ] Lens Shading Correction
- [x] Anti-aliasing Noise Filter
- [x] AWB Gain Control
- [x] Noise Reduction (Bayer Domain)
  - [ ] Luma Denoising
  - [x] Chroma Denoising
- [x] Demosaicing
- [x] Gamma Correction
- [x] Color Correction Matrix
- [x] Color Space Conversion
- [ ] Noise Filter for Luma/Chroma
  - [x] Luma Noise Reduction
    - [x] Bilateral Filtering
    - [x] Non-local Means Denoising
  - [ ] Chroma Noise Reduction
- [x] Edge Enhancement
- [x] False Color Suppression
- [x] Hue/Saturation Control
- [x] Brightness/Contrast Control

## References
[1] Park H.S. (2016) Architectural Analysis of a Baseline ISP Pipeline. In: Kyung CM. (eds) Theory and Applications of Smart Cameras. KAIST Research Series. Springer, Dordrecht.

## File Structure

The openISP project tree structure is listed as follows.

```shell
openISP
│  .gitignore
│  isp_pipeline.py
│  LICENSE
│  README.md
│
├─config
│      config.csv
│
├─docs
│      openISP.md
│
├─hardware
├─images
│      isp_pipeline.png
│      isp_pipeline2.png
│
├─model
│     awb.py
│     bcc.py
│     blc.py
│     bnf.py
│     ccm.py
│     cfa.py
│     cnf.py
│     csc.py
│     dpc.py
│     eeh.py
│     fcs.py
│     gac.py
│     hsc.py
│     nlm.py
│
├─raw
│      test.RAW
│
└─tuning
```

`config` contains config.csv which has all ISP configurable parameters. 

`docs` contains the documentation of ISP, including algorithms introduction and other information. 

`hardware` is remained for the hardware implementation (Verilog/Chisel) of ISP algorithms and SoC.

`images` has all images in *.md files.

`model` is the python implementation of ISP algorithms.

`raw` has *.RAW images of 10/12 bits.

`tuning` is remained for ISP tuning tool, which is in progress.

## Usage

After cloning the repo, just

```python
python isp_pipeline.py
```

It loads `test.raw` image and `config.csv` and executes the algorithms step by step.

You can adjust the ISP pipeline as you want. However, algorithms like DPC, BLC, LSC, ANF, AWB, CFA, only work in Bayer domain. GC, CCM, CSC work in RGB domain. Others work in YUV domain. It's not saying like NF only work in YUV domain. Just in openISP case, it works in YUV domain. Noise filtering could be done in Bayer/RGB/YUV domain and in both temporal/spatial domain.

## License

MIT @Crux