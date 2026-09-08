# -*- coding: utf-8 -*-
"""将语义分割实例转换为污点直径、划痕宽度等工艺尺寸。"""

import math
from typing import Iterable

import cv2
import numpy as np

from backend.inspection_types import DefectMeasurement, SegmentationInstance


def measure_instances(
    instances: Iterable[SegmentationInstance],
    *,
    um_per_pixel: float,
) -> list[DefectMeasurement]:
    """批量测量实例，返回顺序与输入实例顺序完全一致。"""

    return [
        measure_instance(instance, um_per_pixel=um_per_pixel, index=index)
        for index, instance in enumerate(instances)
    ]


def measure_instance(
    instance: SegmentationInstance,
    *,
    um_per_pixel: float,
    index: int = -1,
) -> DefectMeasurement:
    """测量一个分割实例。

    污点后续使用 ``equivalent_diameter_um``：由轮廓面积等效为圆的直径；
    划痕后续使用 ``rotated_width_um``：由旋转最小外接矩形的短边给出。
    本函数不依据类别改变测量方式，使结果可追溯、也方便后续复判。
    """

    result = DefectMeasurement(
        instance_index=index,
        class_id=int(instance.class_id),
        class_name=str(instance.class_name),
    )
    scale = _valid_scale(um_per_pixel)
    if scale is None:
        result.warnings.append("像素标定比例必须是大于 0 的有效 µm/px 数值")
        return result

    contour = _polygon_contour(instance.polygon)
    if contour is not None:
        return _measure_polygon(instance, contour, scale, result)

    result.warnings.append("分割轮廓无效，已使用 bbox 估算尺寸")
    return _measure_bbox(instance, scale, result)


def _measure_polygon(
    instance: SegmentationInstance,
    contour: np.ndarray,
    scale: float,
    result: DefectMeasurement,
) -> DefectMeasurement:
    area_px2 = abs(float(cv2.contourArea(contour)))
    if not math.isfinite(area_px2) or area_px2 <= 0:
        result.warnings.append("分割轮廓面积无效，已使用 bbox 估算尺寸")
        return _measure_bbox(instance, scale, result)

    center = _polygon_centroid(contour)
    if center is None:
        result.warnings.append("分割轮廓质心无效，已使用 bbox 中心")
        center = _bbox_center(instance.bbox)

    rect = cv2.minAreaRect(contour)
    edge_a, edge_b = (float(value) for value in rect[1])
    if not all(math.isfinite(value) and value > 0 for value in (edge_a, edge_b)):
        result.warnings.append("旋转外接矩形无效，已使用 bbox 估算尺寸")
        return _measure_bbox(instance, scale, result)

    _assign_geometry(
        result,
        center=center,
        area_px2=area_px2,
        length_px=max(edge_a, edge_b),
        width_px=min(edge_a, edge_b),
        scale=scale,
        source="polygon",
        estimated=False,
    )
    return result


def _measure_bbox(
    instance: SegmentationInstance,
    scale: float,
    result: DefectMeasurement,
) -> DefectMeasurement:
    bbox = _valid_bbox(instance.bbox)
    if bbox is None:
        result.warnings.append("bbox 无效，无法计算缺陷物理尺寸")
        return result

    x1, y1, x2, y2 = bbox
    width_px = abs(x2 - x1)
    height_px = abs(y2 - y1)
    area_px2 = float(instance.pixel_area)
    if not math.isfinite(area_px2) or area_px2 <= 0:
        area_px2 = width_px * height_px
        result.warnings.append("实例像素面积无效，已用 bbox 面积估算")

    if width_px <= 0 or height_px <= 0 or area_px2 <= 0:
        result.warnings.append("bbox 尺寸无效，无法计算缺陷物理尺寸")
        return result

    _assign_geometry(
        result,
        center=((x1 + x2) / 2.0, (y1 + y2) / 2.0),
        area_px2=area_px2,
        length_px=max(width_px, height_px),
        width_px=min(width_px, height_px),
        scale=scale,
        source="bbox_estimate",
        estimated=True,
    )
    return result


def _assign_geometry(
    result: DefectMeasurement,
    *,
    center: tuple[float, float] | None,
    area_px2: float,
    length_px: float,
    width_px: float,
    scale: float,
    source: str,
    estimated: bool,
) -> None:
    result.center_x_px = None if center is None else center[0]
    result.center_y_px = None if center is None else center[1]
    result.area_px2 = area_px2
    result.area_um2 = area_px2 * scale * scale
    result.equivalent_diameter_um = 2.0 * math.sqrt(
        result.area_um2 / math.pi
    )
    result.rotated_length_um = length_px * scale
    result.rotated_width_um = width_px * scale
    result.source = source
    result.estimated = estimated


def _polygon_contour(points) -> np.ndarray | None:
    if not isinstance(points, list) or len(points) < 3:
        return None
    try:
        contour = np.asarray(points, dtype=np.float32)
    except (TypeError, ValueError):
        return None
    if contour.shape != (len(points), 2) or not np.isfinite(contour).all():
        return None
    return contour.reshape((-1, 1, 2))


def _polygon_centroid(contour: np.ndarray) -> tuple[float, float] | None:
    moments = cv2.moments(contour)
    area = float(moments.get("m00", 0.0))
    if not math.isfinite(area) or math.isclose(area, 0.0, abs_tol=1e-12):
        return None
    center_x = float(moments["m10"]) / area
    center_y = float(moments["m01"]) / area
    if not math.isfinite(center_x) or not math.isfinite(center_y):
        return None
    return center_x, center_y


def _valid_bbox(values) -> tuple[float, float, float, float] | None:
    if not isinstance(values, tuple) or len(values) != 4:
        return None
    try:
        bbox = tuple(float(value) for value in values)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in bbox):
        return None
    return bbox


def _bbox_center(values) -> tuple[float, float] | None:
    bbox = _valid_bbox(values)
    if bbox is None:
        return None
    x1, y1, x2, y2 = bbox
    return (x1 + x2) / 2.0, (y1 + y2) / 2.0


def _valid_scale(value: float) -> float | None:
    try:
        scale = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(scale) or scale <= 0:
        return None
    return scale
