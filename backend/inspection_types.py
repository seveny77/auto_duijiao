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


@dataclass
class RoiRegion:
    """一个端面的实际裁切范围；所有坐标均相对于采集原图，单位 px。

    requested_bbox 保存按圆心和边长取整后、限制图像边界之前的
    (x0, y0, x1, y1)。
    x、y、width、height 保存最终用于 image[y:y+height, x:x+width]
    的整数范围，右边界和下边界不包含在内。source 为 fixed/manual/circle。
    image_width/image_height 为原图尺寸，并非裁切子图尺寸。
    margin_px 只记录上下文外扩量，不改变端面的有效质检区域。
    本对象只保存数据；范围校验和裁切由后续 ROI 模块负责。
    """

    roi_id: str = ""
    circle_id: str = ""
    source: str = "fixed"
    image_width: int = 0
    image_height: int = 0
    requested_bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0
    margin_px: int = 0


@dataclass
class CircleInspectionResult:
    """一个端面的检测结果；circle_id 在同一张图的任务内唯一。

    circle_candidate、instances 的 polygon/bbox 均使用原图坐标，
    pixel_area 也是原图像素坐标下的面积。不得混入 ROI 局部坐标。
    circle_candidate/roi 为 None 表示尚未定位或尚未生成裁切范围；
    固定 ROI 可在没有圆的情况下保留分割结果，圆环判定保持待确认。
    completed 仅表示本端面的处理已结束，不代表合格或圆心已确认；
    例如处理结束但推理失败时，可同时为 completed=True、status=ERROR。
    """

    circle_id: str = ""
    circle_candidate: Optional[CircleCandidate] = None
    roi: Optional[RoiRegion] = None
    circle_confirmed: bool = False
    completed: bool = False
    status: InspectionStatus = InspectionStatus.PENDING
    error: str = ""
    warnings: list[str] = field(default_factory=list)
    instances: list[SegmentationInstance] = field(default_factory=list)
    region_results: list[RegionInspectionResult] = field(default_factory=list)
    failure_reasons: list[str] = field(default_factory=list)
    timings_ms: dict[str, float] = field(default_factory=dict)


@dataclass
class ImageInspectionResult:
    """一张原图内多个端面的结果快照，与旧 InspectionResult 并存。

    expected_circle_count 是预期端面数，detected_circle_count 是定位到的
    有效端面数，completed_circle_count 是处理已结束的端面数。
    is_complete 表示所有预期端面均得到有效、已确认的检测及判定结果。
    因此端面处理结束但出现 ERROR 时，整图仍可为 is_complete=False。
    status 与完成状态独立，允许 status=FAIL 且 is_complete=False，
    表示已有端面确认不合格，但其余端面尚未全部完成有效判定。

    计数、is_complete 和 status 均由后续编排/汇总模块明确填写，
    本数据类不自动找圆或判定；空结果默认待确认，不能自动视为合格。
    mm_per_pixel 延续旧配置字段名和数值约定（界面单位 µm/px），
    此处不进行单位倍率转换。schema_version 供后续历史格式识别使用。
    """

    schema_version: int = 2
    image_id: str = ""
    image_width: int = 0
    image_height: int = 0
    mm_per_pixel: float = 0.0
    expected_circle_count: int = 0
    detected_circle_count: int = 0
    completed_circle_count: int = 0
    is_complete: bool = False
    circle_results: list[CircleInspectionResult] = field(default_factory=list)
    status: InspectionStatus = InspectionStatus.PENDING
    error: str = ""
    warnings: list[str] = field(default_factory=list)
    failure_reasons: list[str] = field(default_factory=list)
    timings_ms: dict[str, float] = field(default_factory=dict)


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
