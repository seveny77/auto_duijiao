# -*- coding: utf-8 -*-
"""语义分割质检模块使用的纯 Python 数据契约。"""

from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from typing import Any, Optional


class InspectionStatus(str, Enum):
    """一次质检任务的总体状态。"""

    PASS = "PASS"
    FAIL = "FAIL"
    PENDING = "PENDING"
    ERROR = "ERROR"


@dataclass
class SegmentationInstance:
    """语义分割模型返回的一个缺陷实例。"""

    class_id: int = -1
    class_name: str = ""
    confidence: float = 0.0
    polygon: list[tuple[float, float]] = field(default_factory=list)
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    pixel_area: int = 0


@dataclass
class CircleCandidate:
    """自动找圆产生的一个候选圆。"""

    center_x: float = 0.0
    center_y: float = 0.0
    radius_px: float = 0.0
    score: float = 0.0
    source: str = ""


@dataclass
class InspectionRegionRule:
    """一个圆环区域内、一个缺陷类别对应的卡控规则。"""

    region_id: str = ""
    region_name: str = ""
    inner_radius_mm: float = 0.0
    outer_radius_mm: float = 0.0
    class_id: int = -1
    class_name: str = ""
    min_confidence: float = 0.0
    min_instance_area_mm2: float = 0.0
    max_instance_count: int = 0


@dataclass
class RegionInspectionResult:
    """一个圆环区域、一个缺陷类别的统计与判定结果。"""

    region_id: str = ""
    region_name: str = ""
    class_id: int = -1
    class_name: str = ""
    valid_instance_count: int = 0
    total_area_mm2: float = 0.0
    passed: bool = True
    failure_reasons: list[str] = field(default_factory=list)


@dataclass
class InspectionResult:
    """一次最终成像质检的完整结果。"""

    status: InspectionStatus = InspectionStatus.PENDING
    error: str = ""
    warnings: list[str] = field(default_factory=list)
    image_width: int = 0
    image_height: int = 0
    mm_per_pixel: float = 0.0
    circle_candidates: list[CircleCandidate] = field(default_factory=list)
    selected_circle_index: Optional[int] = None
    circle_confirmed: bool = False
    instances: list[SegmentationInstance] = field(default_factory=list)
    region_results: list[RegionInspectionResult] = field(default_factory=list)
    failure_reasons: list[str] = field(default_factory=list)
    timings_ms: dict[str, float] = field(default_factory=dict)


@dataclass
class InspectionRecord:
    """保存到历史目录的一次质检记录。"""

    record_id: str = ""
    detected_at: str = ""
    model_name: str = ""
    model_path: str = ""
    original_image_path: str = ""
    overlay_image_path: str = ""
    thumbnail_image_path: str = ""
    result_json_path: str = ""
    result: InspectionResult = field(default_factory=InspectionResult)


def inspection_to_dict(value: Any):
    """递归转换质检数据，使其可以直接交给 ``json.dumps``。"""

    if isinstance(value, Enum):
        return value.value

    if is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: inspection_to_dict(getattr(value, item.name))
            for item in fields(value)
        }

    if isinstance(value, tuple):
        return [inspection_to_dict(item) for item in value]

    if isinstance(value, list):
        return [inspection_to_dict(item) for item in value]

    if isinstance(value, dict):
        return {
            key: inspection_to_dict(item)
            for key, item in value.items()
        }

    return value
