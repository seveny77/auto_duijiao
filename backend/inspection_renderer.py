# -*- coding: utf-8 -*-
"""将质检结果绘制为带缺陷轮廓和文字标签的 BGR 图像。"""

from functools import lru_cache
import os

import cv2
import numpy as np


# 缺陷轮廓统一使用红色；圆、圆心和圆环继续使用各自颜色区分。
CLASS_COLORS_BGR = ((0, 0, 255),)
CIRCLE_COLOR_BGR = (0, 220, 255)
CENTER_COLOR_BGR = (0, 0, 255)
ROI_COLOR_BGR = (255, 200, 0)
RING_COLORS_BGR = (
    (80, 255, 80),
    (0, 165, 255),
    (255, 120, 80),
    (200, 80, 255),
)


def render_inspection_overlay(
    image,
    result,
    config,
    *,
    background: str = "original",
    show_contours: bool = True,
    show_circle: bool = True,
    show_rings: bool = True,
):
    """返回检测结果图，不修改输入图像，也不填充多边形内部。"""

    canvas = _prepare_canvas(image, background)
    height, width = canvas.shape[:2]
    line_width = max(1, round(min(width, height) / 1000))

    if show_contours:
        _draw_instance_contours(canvas, result, config, line_width)

    selected_circle = _selected_circle(result)
    if selected_circle is not None:
        if show_rings:
            _draw_region_rings(
                canvas,
                selected_circle,
                config,
                line_width,
            )
        if show_circle:
            _draw_selected_circle(canvas, selected_circle, line_width)

    return canvas


def render_image_inspection_overlay(
    image,
    result,
    config,
    *,
    background: str = "original",
    show_contours: bool = True,
    show_circle: bool = True,
    show_rings: bool = True,
    show_rois: bool = True,
):
    """绘制一张原图内所有端面，不根据 GUI 当前选择过滤结果。"""

    canvas = _prepare_canvas(image, background)
    height, width = canvas.shape[:2]
    line_width = max(1, round(min(width, height) / 1000))
    circle_results = list(getattr(result, "circle_results", []) or [])

    # 结构辅助线先画，红色缺陷轮廓最后画，避免缺陷被圆环覆盖。
    for circle_result in circle_results:
        roi = getattr(circle_result, "roi", None)
        circle = getattr(circle_result, "circle_candidate", None)
        if show_rois and roi is not None:
            _draw_roi(canvas, roi, line_width)
        if circle is not None:
            if show_rings:
                _draw_region_rings(canvas, circle, config, line_width)
            if show_circle:
                _draw_selected_circle(canvas, circle, line_width)

    if show_contours:
        for circle_result in circle_results:
            _draw_instances(
                canvas,
                getattr(circle_result, "instances", []) or [],
                config,
                line_width,
            )

    return canvas


def _prepare_canvas(image, background: str):
    if image is None or getattr(image, "ndim", 0) not in (2, 3):
        raise ValueError("绘制质检结果需要有效图像")
    if getattr(image, "size", 0) == 0:
        raise ValueError("绘制质检结果收到空图像")

    if image.ndim == 2:
        bgr = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    elif image.shape[2] == 3:
        bgr = image.copy()
    elif image.shape[2] == 4:
        bgr = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
    else:
        raise ValueError("质检图像通道数必须为 1、3 或 4")

    if background == "original":
        return bgr
    if background == "black":
        return np.zeros_like(bgr)
    raise ValueError(f"不支持的绘图背景: {background}")


def _draw_instance_contours(canvas, result, config, line_width: int):
    _draw_instances(
        canvas,
        getattr(result, "instances", []) or [],
        config,
        line_width,
    )


def _draw_instances(canvas, instances, config, line_width: int):
    """画红色轮廓，并为每个有效实例添加类别和物理面积标签。"""

    height, width = canvas.shape[:2]
    labels = []
    for instance in instances:
        points = _polygon_points(
            getattr(instance, "polygon", []),
            width,
            height,
        )
        if points is None:
            continue
        class_id = int(getattr(instance, "class_id", -1))
        color = CLASS_COLORS_BGR[class_id % len(CLASS_COLORS_BGR)]
        cv2.polylines(
            canvas,
            [points],
            isClosed=True,
            color=color,
            thickness=line_width,
            lineType=cv2.LINE_AA,
        )
        labels.append((instance, points))

    _draw_instance_labels(canvas, labels, config, line_width)


def _draw_instance_labels(canvas, labels, config, line_width: int):
    """使用 Windows 中文字体绘制 ``脏污 2.23um²`` 一类标签。"""

    if not labels:
        return

    # inspection_config 的字段名沿用历史 mm_per_pixel，但 GUI 当前单位
    # 明确为 um/px，故面积换算为 pixel_area × (um/px)^2，即 um²。
    um_per_pixel = float(getattr(config, "mm_per_pixel", 0.0) or 0.0)
    if not np.isfinite(um_per_pixel) or um_per_pixel <= 0:
        return

    try:
        from PIL import Image, ImageDraw
    except ImportError:
        # Pillow 是 Ultralytics 的正常依赖；缺失时宁可保留轮廓，也不要让
        # 检测结果页面因绘制文字失败。
        return

    height, width = canvas.shape[:2]
    font_size = max(16, min(36, int(round(min(width, height) / 100))))
    font = _load_chinese_font(font_size)
    if font is None:
        return

    pil_image = Image.fromarray(cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(pil_image)
    padding = max(3, line_width + 1)
    gap = max(4, line_width * 2)

    for instance, points in labels:
        label = _instance_label(instance, points, um_per_pixel)
        if not label:
            continue

        left = int(points[:, 0, 0].min())
        top = int(points[:, 0, 1].min())
        right = int(points[:, 0, 0].max())
        bottom = int(points[:, 0, 1].max())
        text_box = draw.textbbox((0, 0), label, font=font)
        text_width = text_box[2] - text_box[0]
        text_height = text_box[3] - text_box[1]
        x = min(max(0, left), max(0, width - text_width - padding * 2))
        y = top - text_height - padding * 2 - gap
        if y < 0:
            y = min(height - text_height - padding * 2, bottom + gap)

        draw.rounded_rectangle(
            (
                x,
                y,
                x + text_width + padding * 2,
                y + text_height + padding * 2,
            ),
            radius=padding,
            fill=(20, 20, 20),
        )
        draw.text(
            (x + padding, y + padding - text_box[1]),
            label,
            font=font,
            fill=(255, 255, 255),
        )

    canvas[:, :] = cv2.cvtColor(np.asarray(pil_image), cv2.COLOR_RGB2BGR)


def _instance_label(instance, points, um_per_pixel: float) -> str:
    """由实例类别、像素面积和当前标定比例构造面向操作者的标签。"""

    class_name = str(getattr(instance, "class_name", "") or "").strip()
    if not class_name:
        class_name = f"类别 {int(getattr(instance, 'class_id', -1))}"

    pixel_area = float(getattr(instance, "pixel_area", 0) or 0)
    if not np.isfinite(pixel_area) or pixel_area <= 0:
        pixel_area = float(cv2.contourArea(points))
    area_um2 = max(0.0, pixel_area) * um_per_pixel * um_per_pixel
    return f"{class_name} {area_um2:.2f}um²"


@lru_cache(maxsize=8)
def _load_chinese_font(font_size: int):
    """优先使用 Windows 中文字体；避免每个缺陷标签重复加载字体文件。"""

    try:
        from PIL import ImageFont
    except ImportError:
        return None

    candidates = (
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
    )
    for path in candidates:
        if os.path.isfile(path):
            return ImageFont.truetype(path, font_size)
    return None


def _draw_roi(canvas, roi, line_width: int):
    height, width = canvas.shape[:2]
    try:
        left = int(getattr(roi, "x"))
        top = int(getattr(roi, "y"))
        roi_width = int(getattr(roi, "width"))
        roi_height = int(getattr(roi, "height"))
    except (TypeError, ValueError):
        return
    if roi_width <= 0 or roi_height <= 0:
        return

    right = min(width - 1, left + roi_width - 1)
    bottom = min(height - 1, top + roi_height - 1)
    left = max(0, left)
    top = max(0, top)
    if left > right or top > bottom:
        return
    cv2.rectangle(
        canvas,
        (left, top),
        (right, bottom),
        ROI_COLOR_BGR,
        line_width,
        cv2.LINE_AA,
    )


def _polygon_points(polygon, width: int, height: int):
    try:
        points = np.asarray(polygon, dtype=np.float64).reshape(-1, 2)
    except (TypeError, ValueError):
        return None
    if len(points) < 3 or not np.isfinite(points).all():
        return None
    points[:, 0] = np.clip(np.rint(points[:, 0]), 0, width - 1)
    points[:, 1] = np.clip(np.rint(points[:, 1]), 0, height - 1)
    return points.astype(np.int32).reshape(-1, 1, 2)


def _selected_circle(result):
    candidates = getattr(result, "circle_candidates", []) or []
    index = getattr(result, "selected_circle_index", None)
    if index is None or not isinstance(index, int):
        return None
    if index < 0 or index >= len(candidates):
        return None
    return candidates[index]


def _draw_selected_circle(canvas, circle, line_width: int):
    center = (
        int(round(getattr(circle, "center_x", 0.0))),
        int(round(getattr(circle, "center_y", 0.0))),
    )
    radius = int(round(getattr(circle, "radius_px", 0.0)))
    if radius > 0:
        cv2.circle(
            canvas,
            center,
            radius,
            CIRCLE_COLOR_BGR,
            line_width,
            cv2.LINE_AA,
        )
    marker_size = max(8, line_width * 5)
    cv2.drawMarker(
        canvas,
        center,
        CENTER_COLOR_BGR,
        markerType=cv2.MARKER_CROSS,
        markerSize=marker_size,
        thickness=line_width,
        line_type=cv2.LINE_AA,
    )


def _draw_region_rings(canvas, circle, config, line_width: int):
    mm_per_pixel = float(getattr(config, "mm_per_pixel", 0.0) or 0.0)
    if not np.isfinite(mm_per_pixel) or mm_per_pixel <= 0:
        return

    center = (
        int(round(getattr(circle, "center_x", 0.0))),
        int(round(getattr(circle, "center_y", 0.0))),
    )
    radii_mm = sorted({
        float(getattr(rule, "outer_radius_mm", 0.0))
        for rule in (getattr(config, "region_rules", []) or [])
        if float(getattr(rule, "outer_radius_mm", 0.0)) > 0
    })
    for index, radius_mm in enumerate(radii_mm):
        radius_px = int(round(radius_mm / mm_per_pixel))
        if radius_px <= 0:
            continue
        cv2.circle(
            canvas,
            center,
            radius_px,
            RING_COLORS_BGR[index % len(RING_COLORS_BGR)],
            line_width,
            cv2.LINE_AA,
        )
