# backend/result.py
"""对焦结果：后端返回的类型化对象。"""

from dataclasses import dataclass, field
from typing import Optional


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
    ct_ms: dict = field(default_factory=dict)


@dataclass
class CalibrateResult:
    rc: int = 0
    action: str = "calibrate"
    error: str = ""
    peak_position: int = -1
    peak_width: float = 0.0
    peak_um: float = 0.0
    ct_ms: dict = field(default_factory=dict)
