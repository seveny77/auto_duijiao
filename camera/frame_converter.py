from MvImport.MvCameraControl_class import *
import numpy as np
import cv2

def _frame_to_numpy(stFrame):
    w = stFrame.stFrameInfo.nWidth
    h = stFrame.stFrameInfo.nHeight
    length = stFrame.stFrameInfo.nFrameLen

    buf = (c_ubyte * length)()
    memmove(buf, stFrame.pBufAddr, length)
    raw = np.frombuffer(buf, dtype=np.uint8)

    pix_type = stFrame.stFrameInfo.enPixelType
    if pix_type == PixelType_Gvsp_Mono8:
        gray = raw.reshape(h, w)
        img = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    elif pix_type == PixelType_Gvsp_BGR8_Packed:
        img = raw.reshape(h, w, 3)
    else:
        # 如需YUV/Bayer自行扩展
        gray = raw.reshape(h, w)
        img = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    return img