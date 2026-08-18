import os
import sys
import time
import threading
import queue
import numpy as np
import cv2
from ctypes import *

# 优先加载 MVS 运行时 DLL，避免加载到其他旧版 MvCameraControl.dll（如 LBAS 运行时）
_MVS_RUNTIME_DIRS = [
    r"C:\Program Files (x86)\Common Files\MVS\Runtime\Win64_x64",
    r"C:\Program Files\Common Files\MVS\Runtime\Win64_x64",
]
for _dll_dir in _MVS_RUNTIME_DIRS:
    if os.path.isdir(_dll_dir):
        try:
            os.add_dll_directory(_dll_dir)
        except Exception:
            pass
        os.environ["PATH"] = _dll_dir + os.pathsep + os.environ.get("PATH", "")
        break

from MvImport.MvCameraControl_class import *
from MvImport.CameraParams_header import MVCC_INTVALUE
from camera.frame_converter import _frame_to_numpy
_sdk_initialized = False

class HikCamera:
    def __init__(self, camera_index=0):
        self._cam = None
        self._index = camera_index
        self._trigger_mode = "off"
        self._device_info = None
        self._stream_count = 0
        self._user_callback = None
        self._cb_func_holder = None
        self._is_grabbing = False

    # ========== lifecycle ==========
    def open(self):
        ret = MvCamera.MV_CC_Initialize()
        deviceList = MV_CC_DEVICE_INFO_LIST()
        MvCamera.MV_CC_EnumDevices(MV_USB_DEVICE, deviceList)
        if deviceList.nDeviceNum <= self._index:
            raise RuntimeError("camera not found")
        self._device_info = cast(
            deviceList.pDeviceInfo[self._index], POINTER(MV_CC_DEVICE_INFO)
        ).contents
        self._cam = MvCamera()
        ret = self._cam.MV_CC_CreateHandle(self._device_info)
        if ret != 0:
            raise RuntimeError(f"CreateHandle fail: 0x{ret:X}")
        ret = self._cam.MV_CC_OpenDevice(MV_ACCESS_Exclusive, 0)
        if ret != 0:
            raise RuntimeError(f"OpenDevice fail: 0x{ret:X}")
        print(f"camera opened: {self._model_name()}")

    def close(self):
        if self._is_grabbing:
            self._cam.MV_CC_StopGrabbing()
            self._is_grabbing = False
        if self._cam:
            self._cam.MV_CC_CloseDevice()
            self._cam.MV_CC_DestroyHandle()
            self._cam = None
        self._cb_func_holder = None
        #MvCamera.MV_CC_Finalize()
        self._cb_func_holder = None
        print("camera closed")

    @staticmethod
    def shutdown():
        """整个程序退出时调用一次"""
        global _sdk_initialized
        if _sdk_initialized:
            MvCamera.MV_CC_Finalize()
            _sdk_initialized = False

    def _model_name(self):
        raw = bytes(self._device_info.SpecialInfo.stUsb3VInfo.chModelName)
        return raw.decode("gbk").strip("\x00")

    # ========== settings ==========
    def set_exposure(self, value_us):
        self._cam.MV_CC_SetFloatValue("ExposureTime", float(value_us))
        print(f"exposure: {value_us}us")

    def set_gain(self, value_db):
        self._cam.MV_CC_SetFloatValue("Gain", float(value_db))
        print(f"gain: {value_db}dB")

    def set_roi(self, x, y, w, h):
        if self._is_grabbing:
            raise RuntimeError("stop grabbing before ROI change")
        # 顺序：先清偏移 → 设宽高 → 再设偏移。
        # Offset 范围动态 = 传感器 − 窗口：扩大窗口（如恢复全幅）时若偏移非零会失败，
        # 所以先归零；缩窗口时最后设偏移。两种方向都成立。
        for key, val in (("OffsetX", 0), ("OffsetY", 0),
                         ("Width", w), ("Height", h),
                         ("OffsetX", x), ("OffsetY", y)):
            ret = self._cam.MV_CC_SetIntValue(key, val)
            if ret != 0:
                print(f"[警告] {key}={val} 设置失败 0x{ret:X}")
        print(f"ROI: ({x},{y}) {w}x{h}")

    def set_binning(self, h_bin, v_bin):
        if self._is_grabbing:
            raise RuntimeError("stop grabbing before binning change")
        # 本相机（MV-CU050-90UM）使用厂商 _Val 节点；其他型号回退标准节点
        for axis, val in (("Horizontal", h_bin), ("Vertical", v_bin)):
            ok = False
            for name in (f"Binning{axis}_Val", f"Binning{axis}"):
                ret = self._cam.MV_CC_SetIntValue(name, val)
                if ret == 0:
                    ok = True
                    break
                print(f"[警告] {name}={val} 设置失败 0x{ret:X}")
            if not ok:
                print(f"[错误] binning {axis} 设置失败")
        # 读回实际生效值
        for axis in ("Horizontal", "Vertical"):
            st = MVCC_INTVALUE()
            ret = self._cam.MV_CC_GetIntValue(f"Binning{axis}_Val", st)
            if ret == 0:
                print(f"binning {axis} 实际: {st.nCurValue}")
        print(f"binning: {h_bin}x{v_bin}")

    def set_decimation(self, h_dec, v_dec):
        """Decimation 硬件降采样（抽行抽列，亮度不变）。优先 _Val 节点，失败回退标准名。"""
        if self._is_grabbing:
            raise RuntimeError("stop grabbing before decimation change")
        for axis, val in (("Horizontal", h_dec), ("Vertical", v_dec)):
            ok = False
            for name in (f"Decimation{axis}_Val", f"Decimation{axis}"):
                ret = self._cam.MV_CC_SetIntValue(name, val)
                if ret == 0:
                    ok = True
                    break
                print(f"[警告] {name}={val} 设置失败 0x{ret:X}")
            if not ok:
                print(f"[错误] decimation {axis} 设置失败")
        for axis in ("Horizontal", "Vertical"):
            st = MVCC_INTVALUE()
            ret = self._cam.MV_CC_GetIntValue(f"Decimation{axis}_Val", st)
            if ret == 0:
                print(f"decimation {axis} 实际: {st.nCurValue}")
        print(f"decimation: {h_dec}x{v_dec}")

    # ========== trigger ==========
    def set_trigger_mode(self, mode="off"):
        if self._is_grabbing:
            raise RuntimeError("stop grabbing before trigger mode change")
        mapping = {
            "off": MV_TRIGGER_MODE_OFF,
            "software": MV_TRIGGER_MODE_ON,
            "hardware": MV_TRIGGER_MODE_ON,
        }
        self._cam.MV_CC_SetEnumValue("TriggerMode", mapping[mode])
        if mode == "software":
            self._cam.MV_CC_SetEnumValue("TriggerSource", MV_TRIGGER_SOURCE_SOFTWARE)
        elif mode == "hardware":
            self._cam.MV_CC_SetEnumValue("TriggerSource", MV_TRIGGER_SOURCE_LINE0)
        self._trigger_mode = mode
        print(f"trigger: {mode}")

    def trigger_software(self):
        ret = self._cam.MV_CC_SetCommandValue("TriggerSoftware")
        if ret != 0:
            print(f"soft trigger fail: 0x{ret:X}")
        return ret

    # ========== sync grab (GetImageBuffer) ==========
    def capture_frame(self, timeout_ms=500) -> np.ndarray:
        if self._cam is None: raise RuntimeError("相机未打开，请先调用 open()")
        self.set_trigger_mode("software")
        self.start_grabbing()
        try:
            self.trigger_software()
            img =  self.get_frame(timeout_ms)
            if img is None:
                raise RuntimeError(f"软触发取帧超时（{timeout_ms}ms）")
            return img
        finally:
            self.stop_grabbing()


    def start_grabbing(self):
        ret = self._cam.MV_CC_StartGrabbing()
        if ret == 0:
            self._is_grabbing = True
            print("grabbing started (sync)")
        else:
            raise RuntimeError(f"StartGrabbing fail 0x{ret:X}")

    def stop_grabbing(self):
        self._cam.MV_CC_StopGrabbing()
        self._is_grabbing = False
        print("grabbing stopped")

    def get_frame(self, timeout_ms=200):
        f = MV_FRAME_OUT()
        memset(byref(f), 0, sizeof(f))
        ret = self._cam.MV_CC_GetImageBuffer(f, timeout_ms)
        if ret != 0:
            return None
        img = _frame_to_numpy(f)
        self._cam.MV_CC_FreeImageBuffer(f)
        return img

    # ========== async callback (RegisterImageCallBackEx) ==========
    def register_frame_callback(self, callback):
        """Register callback. callback(img_bgr) runs in SDK thread."""
        self._user_callback = callback
        self._register_callback()

    def _register_callback(self):
        """RegisterImageCallBackEx (Ex API). Ex2 incompatible with soft trigger."""
        if self._cb_func_holder is not None:
            return

        def sdk_callback(pData, pFrameInfo, pUser):
            if pData is None:
                return
            fi = cast(pFrameInfo, POINTER(MV_FRAME_OUT_INFO_EX)).contents
            buf = string_at(pData, fi.nFrameLen)
            img = np.frombuffer(buf, dtype=np.uint8)
            try:
                if fi.enPixelType == PixelType_Gvsp_Mono8:
                    img = cv2.cvtColor(img.reshape(fi.nHeight, fi.nWidth), cv2.COLOR_GRAY2BGR)
                elif fi.enPixelType == PixelType_Gvsp_BGR8_Packed:
                    img = img.reshape(fi.nHeight, fi.nWidth, 3)
                else:
                    img = cv2.cvtColor(img.reshape(fi.nHeight, fi.nWidth), cv2.COLOR_GRAY2BGR)
            except Exception as e:
                print(f"cb fmt err: {e}")
                return
            self._stream_count += 1
            try:
                self._user_callback(img)
            except Exception as e:
                print(f"cb err: {e}")

        CB = CFUNCTYPE(None, POINTER(c_ubyte), POINTER(MV_FRAME_OUT_INFO_EX), c_void_p)
        self._cb_func_holder = CB(sdk_callback)

        ret = self._cam.MV_CC_RegisterImageCallBackEx(self._cb_func_holder, None)
        if ret != 0:
            raise RuntimeError(f"RegisterImageCallBackEx fail 0x{ret:X}")
        print("callback registered (Ex)")
