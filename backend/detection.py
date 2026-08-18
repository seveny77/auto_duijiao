from backend.camera_utils import (
     box_to_roi, fallback_roi
)
import os
from typing import Optional, Tuple

_detect_model_cache = {}
_torch_threads_limited = False

def _get_detect_model(model_path: str):
    """模型单例：整个进程只加载一次，避免重复加载导致原生崩溃。"""
    global _torch_threads_limited
    if model_path not in _detect_model_cache:
        import torch
        if not _torch_threads_limited:
            torch.set_num_threads(4)      # 限制 PyTorch 线程数，避免与 Qt/相机线程冲突
            _torch_threads_limited = True
        from ultralytics import YOLO
        _detect_model_cache[model_path] = YOLO(model_path)
    return _detect_model_cache[model_path]
def detect_roi(
    image,
    model_path: str,
    conf: float,
    binning: int,
    fallback_size: int,
    model=None,
) -> Tuple[Tuple[int, int, int, int], str, Optional[Tuple[float, float, float, float]]]:
    """YOLO 检测定 ROI；失败或模型缺失 → 降级居中固定 ROI。

    返回 (roi, 来源, 原始检测框)；原始框在降采样图像坐标系，无检测时返回 None。
    """
    if model is not None:
        try:
            results = model.predict(source=image, conf=conf, verbose=False)
            boxes = results[0].boxes
            if boxes is not None and len(boxes) > 0:
                x1, y1, x2, y2 = boxes.xyxy[0].tolist()
                return box_to_roi(x1, y1, x2, y2, binning), "detect", (x1, y1, x2, y2)
        except Exception as e:
            print(f"[警告] 检测失败(外部模型): {e}")
    elif model_path and os.path.exists(model_path):
        try:
            from ultralytics import YOLO

            model = _get_detect_model(model_path)
            results = model.predict(source=image, conf=conf, verbose=False)
            boxes = results[0].boxes
            if boxes is not None and len(boxes) > 0:
                x1, y1, x2, y2 = boxes.xyxy[0].tolist()
                return box_to_roi(x1, y1, x2, y2, binning), "detect", (x1, y1, x2, y2)
        except Exception as e:
            print(f"[警告] 检测失败: {e}")
    return fallback_roi(fallback_size), "fallback", None