"""连续自动对焦返回给 GUI 的结果对象。"""

from dataclasses import dataclass, field
from typing import Optional

from backend.focus_roi import EvaluationRoi


@dataclass
class BestFrameReady:
    """最佳帧已确定、轴尚在回扫描起点时发布的事件。"""

    image: Optional[object] = None
    best_index: int = -1
    best_score: float = 0.0
    evaluation_roi: Optional[EvaluationRoi] = None
    focus_ct_ms: dict = field(default_factory=dict)
    scan_end_position_um: float = 0.0
    return_target_um: float = 0.0
    final_image_path: Optional[str] = None


@dataclass
class SearchResult:
    """一轮连续扫描的最终结果。"""

    rc: int = 0
    action: str = "search"
    error: str = ""
    quality: str = "continuous_best_frame"
    best_frame_index: int = -1
    best_score: float = 0.0
    final_position_um: float = 0.0
    final_image: Optional[object] = None
    evaluation_roi: Optional[EvaluationRoi] = None
    ct_ms: dict = field(default_factory=dict)
    final_image_path: Optional[str] = None
