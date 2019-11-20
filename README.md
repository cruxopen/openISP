# Open Image Signal Processor (openISP)

## Introduction

Image Signal Processor (ISP) is an application processor to do digital image processing, specifically for conversion from RAW image (acquired from Imaging Sensors) to RGB/YUV image (to further processing or display).

## Objectives

This project aims to provide an overview of ISP and stimulate the whole ISP pipeline and some tuning functions from hardware perspectives. The proposed ISP pipeline consists of following modules, dead pixel correction (DPC), black level compensation (BLC), lens shading correction (LSC), anti-aliasing noise filter (ANF), auto white balance gain control (AWB), color filter array interpolation (CFA), gamma correction (GC), color correction matrix (CCM), color space conversion (CSC), noise filter for luma and chroma (NF), edge enhancement (EE), false color suppression (FCS), hue/saturation/control (HSC) and brightness/contast control (BCC). The ISP pipeline is referred from [1].

Some advanced functions like wide/high dynamic range (W/HDR) and temporal/spatial noise filter (T/SNF) will be implemented in the future.

## References
[1] Park H.S. (2016) Architectural Analysis of a Baseline ISP Pipeline. In: Kyung CM. (eds) Theory and Applications of Smart Cameras. KAIST Research Series. Springer, Dordrecht.
