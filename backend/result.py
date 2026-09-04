# backend/result.py
"""对焦结果：后端返回的类型化对象。"""

from dataclasses import dataclass, field
from typing import Optional

from backend.focus_roi import EvaluationRoi


@dataclass
class BestFrameReady:
    """连续精扫已确定最佳帧、但轴仍在回起点时发布的事件。"""

    image: Optional[object] = None
    best_index: int = -1
    best_score: float = 0.0
    evaluation_roi: Optional[EvaluationRoi] = None
    focus_ct_ms: dict = field(default_factory=dict)
    scan_end_position_um: float = 0.0
    return_target_um: float = 0.0
    # 原图预定的绝对保存路径；事件先于落盘发布，写盘失败仍保留供结果图配对。
    final_image_path: Optional[str] = None


@dataclass
class SearchResult:
    rc: int = 0
    action: str = "search"
    error: str = ""
    predicted_peak_um: float = 0.0
    ncc_max: float = 0.0
    quality: str = ""
    fine_best: int = -1
    final_position_um: float = 0.0
    fine_best_image: Optional[object] = None
    final_image: Optional[object] = None
    coarse_points: list = field(default_factory=list)
    fine_points: list = field(default_factory=list)
    roi: Optional[tuple] = None
    roi_src: str = ""
    detect_box: Optional[tuple] = None
    # 清晰度评价 ROI 使用硬件 ROI 图像内的局部像素坐标。
    # 它只用于评价和 GUI 图元，不允许绘制进 final_image 原图。
    evaluation_roi: Optional[EvaluationRoi] = None
    ct_ms: dict = field(default_factory=dict)
    # 原图预定的绝对保存路径；不代表原图已成功写盘，未启用保存时为 None。
    final_image_path: Optional[str] = None


@dataclass
class CalibrateResult:
    rc: int = 0
    action: str = "calibrate"
    error: str = ""
    peak_position: int = -1
    peak_width: float = 0.0
    peak_um: float = 0.0
    ct_ms: dict = field(default_factory=dict)
