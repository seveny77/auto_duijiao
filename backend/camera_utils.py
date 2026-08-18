from backend.constants import SENSOR_W, SENSOR_H
from autofocus_controller import AutofocusController

def set_full_frame(cam, binning: int):
    """先复位 binning1 + decimation1 + 全幅 → 切目标 binning → 设目标 binning 全幅。

    相机跨会话记住窗口；若直接在小窗口下切 binning，binning 会作用到旧窗口
    （如 700×700 → 348×348）。同时必须复位 decimation，否则其残留会限制
    Width 上限（如 decimation 4 → 最大 648），导致精扫开窗失败。
    """
    cam.set_binning(1, 1)
    cam.set_decimation(1, 1)
    cam.set_roi(0, 0, SENSOR_W, SENSOR_H)
    cam.set_binning(binning, binning)
    w = (SENSOR_W // binning) // 4 * 4
    h = (SENSOR_H // binning) // 4 * 4
    cam.set_roi(0, 0, w, h)

def set_coarse_frame(cam, mode: str, factor: int):
    """粗扫降采样配置：decimation（抽像素，亮度不变）或 binning（求和，会放大亮度）。
    先复位 binning1 + decimation1 + 全幅 → 按模式设 factor → 设该 factor 下全幅窗口。"""
    cam.set_binning(1, 1)
    cam.set_decimation(1, 1)
    cam.set_roi(0, 0, SENSOR_W, SENSOR_H)
    if mode == "decimation":
        cam.set_decimation(factor, factor)
    else:
        cam.set_binning(factor, factor)
    w = (SENSOR_W // factor) // 4 * 4
    h = (SENSOR_H // factor) // 4 * 4
    cam.set_roi(0, 0, w, h)

def box_to_roi(x1, y1, x2, y2, binning):
    roi = (int(round(x1*binning)), int(round(y1*binning)),
           int(round((x2-x1)*binning)), int(round((y2-y1)*binning)))
    return AutofocusController._align_window(roi, sensor_size=(SENSOR_W, SENSOR_H))

def fallback_roi(size):
    x = (SENSOR_W - size) // 2
    y = (SENSOR_H - size) // 2
    return AutofocusController._align_window((x, y, size, size), sensor_size=(SENSOR_W, SENSOR_H))

def frame_positions(n: int, start_um: int, step_um: int) -> list[float]:
    """返回 n 帧的 µm 位置列表（含尾不含首：第 k 帧 = start + (k+1)*step）。"""
    return [start_um+step_um*(k+1) for k in range(n)]

