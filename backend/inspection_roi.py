# -*- coding: utf-8 -*-
"""基于圆心的固定正方形 ROI、子图裁切与原图坐标恢复。"""

import math

from backend.inspection_types import (
    CircleCandidate,
    RoiRegion,
    SegmentationInstance,
)


def build_circle_roi(
    circle: CircleCandidate,
    *,
    circle_id: str,
    roi_size_px: int,
    image_width: int,
    image_height: int,
) -> RoiRegion:
    """以一个圆心生成固定边长正方形 ROI；圆半径不参与尺寸计算。"""

    _validate_image_size(image_width, image_height)
    _validate_roi_size(roi_size_px)
    if not isinstance(circle, CircleCandidate):
        raise TypeError("circle 必须是 CircleCandidate")

    normalized_circle_id = str(circle_id).strip()
    if not normalized_circle_id:
        raise ValueError("circle_id 不能为空")

    center_x = float(circle.center_x)
    center_y = float(circle.center_y)
    if not math.isfinite(center_x) or not math.isfinite(center_y):
        raise ValueError("候选圆心必须是有限数值")
    if not 0 <= center_x < image_width or not 0 <= center_y < image_height:
        raise ValueError("候选圆心必须位于原图范围内")

    requested_x = math.floor(center_x - roi_size_px / 2.0)
    requested_y = math.floor(center_y - roi_size_px / 2.0)
    requested_right = requested_x + roi_size_px
    requested_bottom = requested_y + roi_size_px

    actual_x = max(0, requested_x)
    actual_y = max(0, requested_y)
    actual_right = min(image_width, requested_right)
    actual_bottom = min(image_height, requested_bottom)
    actual_width = actual_right - actual_x
    actual_height = actual_bottom - actual_y
    if actual_width <= 0 or actual_height <= 0:
        raise ValueError("圆心生成的 ROI 与原图没有有效交集")

    return RoiRegion(
        roi_id=f"roi-{normalized_circle_id}",
        circle_id=normalized_circle_id,
        source="circle",
        image_width=image_width,
        image_height=image_height,
        requested_bbox=(
            float(requested_x),
            float(requested_y),
            float(requested_right),
            float(requested_bottom),
        ),
        x=actual_x,
        y=actual_y,
        width=actual_width,
        height=actual_height,
        margin_px=0,
    )


def build_circle_rois(
    circles: list[CircleCandidate],
    *,
    roi_size_px: int,
    image_width: int,
    image_height: int,
) -> list[RoiRegion]:
    """按输入顺序生成多个 ROI；编号仅在本张图的本次任务中稳定。"""

    _validate_image_size(image_width, image_height)
    _validate_roi_size(roi_size_px)
    if not isinstance(circles, list):
        raise TypeError("circles 必须是 CircleCandidate 列表")

    rois = []
    for index, circle in enumerate(circles, start=1):
        try:
            roi = build_circle_roi(
                circle,
                circle_id=f"circle-{index:03d}",
                roi_size_px=roi_size_px,
                image_width=image_width,
                image_height=image_height,
            )
        except (TypeError, ValueError) as error:
            raise type(error)(f"候选圆[{index - 1}]生成 ROI 失败: {error}") from error
        rois.append(roi)
    return rois


def is_roi_clipped(roi: RoiRegion) -> bool:
    """返回实际裁切范围是否小于请求的固定正方形。"""

    _validate_roi_region(roi)
    requested_x, requested_y, requested_right, requested_bottom = (
        roi.requested_bbox
    )
    return not (
        math.isclose(requested_x, float(roi.x), abs_tol=1e-9)
        and math.isclose(requested_y, float(roi.y), abs_tol=1e-9)
        and math.isclose(
            requested_right,
            float(roi.x + roi.width),
            abs_tol=1e-9,
        )
        and math.isclose(
            requested_bottom,
            float(roi.y + roi.height),
            abs_tol=1e-9,
        )
    )


def crop_roi(image, roi: RoiRegion):
    """根据实际范围返回独立子图；不修改或共享原图像素。"""

    _validate_roi_region(roi)
    if image is None or getattr(image, "ndim", 0) not in (2, 3):
        raise ValueError("ROI 裁切需要有效的灰度图或多通道图像")
    if getattr(image, "size", 0) == 0:
        raise ValueError("ROI 裁切收到空图像")

    image_height, image_width = int(image.shape[0]), int(image.shape[1])
    if (image_width, image_height) != (roi.image_width, roi.image_height):
        raise ValueError("ROI 记录的原图尺寸与当前图像不一致")

    patch = image[roi.y:roi.y + roi.height, roi.x:roi.x + roi.width]
    if patch.size == 0 or patch.shape[:2] != (roi.height, roi.width):
        raise ValueError("ROI 实际范围无法生成完整子图")
    return patch.copy()


def restore_instance_to_image(
    instance: SegmentationInstance,
    roi: RoiRegion,
) -> SegmentationInstance:
    """返回平移到原图坐标的新实例，不修改传入实例。"""

    _validate_roi_region(roi)
    if not isinstance(instance, SegmentationInstance):
        raise TypeError("instance 必须是 SegmentationInstance")

    polygon = []
    for index, point in enumerate(instance.polygon):
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError(f"polygon 点[{index}]格式无效")
        x_value, y_value = float(point[0]), float(point[1])
        if not math.isfinite(x_value) or not math.isfinite(y_value):
            raise ValueError(f"polygon 点[{index}]必须是有限数值")
        polygon.append((x_value + roi.x, y_value + roi.y))

    if len(instance.bbox) != 4:
        raise ValueError("bbox 必须包含四个坐标")
    bbox_values = tuple(float(value) for value in instance.bbox)
    if not all(math.isfinite(value) for value in bbox_values):
        raise ValueError("bbox 必须是有限数值")
    x1, y1, x2, y2 = bbox_values

    return SegmentationInstance(
        class_id=instance.class_id,
        class_name=instance.class_name,
        confidence=instance.confidence,
        polygon=polygon,
        bbox=(x1 + roi.x, y1 + roi.y, x2 + roi.x, y2 + roi.y),
        pixel_area=instance.pixel_area,
    )


def restore_instances_to_image(
    instances: list[SegmentationInstance],
    roi: RoiRegion,
) -> list[SegmentationInstance]:
    """批量恢复实例坐标，并为每个实例创建新对象。"""

    if not isinstance(instances, list):
        raise TypeError("instances 必须是 SegmentationInstance 列表")
    return [restore_instance_to_image(instance, roi) for instance in instances]


def _validate_image_size(image_width: int, image_height: int):
    for name, value in (("image_width", image_width), ("image_height", image_height)):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} 必须是大于 0 的整数")


def _validate_roi_size(roi_size_px: int):
    if (
        isinstance(roi_size_px, bool)
        or not isinstance(roi_size_px, int)
        or roi_size_px <= 0
    ):
        raise ValueError("roi_size_px 必须是大于 0 的整数")


def _validate_roi_region(roi: RoiRegion):
    if not isinstance(roi, RoiRegion):
        raise TypeError("roi 必须是 RoiRegion")
    _validate_image_size(roi.image_width, roi.image_height)
    for name, value in (
        ("x", roi.x),
        ("y", roi.y),
        ("width", roi.width),
        ("height", roi.height),
    ):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"ROI {name} 必须是整数")
    if roi.x < 0 or roi.y < 0 or roi.width <= 0 or roi.height <= 0:
        raise ValueError("ROI 实际范围必须位于原图内且尺寸大于 0")
    if roi.x + roi.width > roi.image_width:
        raise ValueError("ROI 实际范围超出原图宽度")
    if roi.y + roi.height > roi.image_height:
        raise ValueError("ROI 实际范围超出原图高度")
    if len(roi.requested_bbox) != 4:
        raise ValueError("ROI requested_bbox 必须包含四个坐标")
    requested_values = tuple(float(value) for value in roi.requested_bbox)
    if not all(math.isfinite(value) for value in requested_values):
        raise ValueError("ROI requested_bbox 必须是有限数值")
    if (
        requested_values[2] <= requested_values[0]
        or requested_values[3] <= requested_values[1]
    ):
        raise ValueError("ROI requested_bbox 的右下边界必须大于左上边界")
