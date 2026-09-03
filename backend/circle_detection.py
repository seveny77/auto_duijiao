# -*- coding: utf-8 -*-
"""使用局部背景校正和轮廓分析定位最终成像中的产品圆。"""

import math
from typing import Optional

from backend.inspection_config import CircleDetectionConfig
from backend.inspection_types import CircleCandidate


# 这些是第一版固定的形状约束。它们不进入 GUI，避免一次引入过多参数。
_MIN_CIRCULARITY = 0.55
_MIN_ASPECT_RATIO = 0.75
_MAX_ASPECT_RATIO = 1.30
_BACKGROUND_SIGMA_RATIO = 1.20
_OPEN_KERNEL_RADIUS_RATIO = 0.20


class ContourCircleDetector:
    """从不均匀背景中分离深色端面，再按轮廓形状筛选产品圆。"""

    def detect(
        self,
        image,
        config: CircleDetectionConfig,
    ) -> tuple[
        list[CircleCandidate],
        Optional[int],
        bool,
        list[str],
    ]:
        """返回按位置排列的选中圆、最高分序号、整体确认状态和警告。"""

        validation_errors = _validate_detection_config(config)
        if validation_errors:
            raise ValueError("；".join(validation_errors))

        # 延迟导入，避免仅导入配置或业务类型时加载 OpenCV/NumPy。
        import cv2
        import numpy as np

        grayscale = _prepare_grayscale(image, cv2, np)
        factor = int(config.downsample_factor)
        if factor > 1:
            small_width = max(1, int(round(grayscale.shape[1] / factor)))
            small_height = max(1, int(round(grayscale.shape[0] / factor)))
            grayscale = cv2.resize(
                grayscale,
                (small_width, small_height),
                interpolation=cv2.INTER_AREA,
            )

        blurred = cv2.GaussianBlur(
            grayscale,
            (config.blur_kernel_size, config.blur_kernel_size),
            0,
        )
        small_min_radius = config.min_radius_px / factor
        small_max_radius = config.max_radius_px / factor
        # 用几何平均而不是算术平均估计预处理尺度。这样旧配置里
        # 100～2000 px 这类很宽的范围不会产生异常巨大的高斯核。
        reference_radius = math.sqrt(
            max(1.0, small_min_radius) * small_max_radius
        )

        # 用大尺度模糊估计局部背景，再与小尺度模糊图相减。
        # 这样深色端面会成为亮前景，能抵抗原图从左到右的亮度变化。
        background_sigma = max(
            20.0,
            reference_radius * _BACKGROUND_SIGMA_RATIO,
        )
        background = cv2.GaussianBlur(
            blurred,
            (0, 0),
            sigmaX=background_sigma,
            sigmaY=background_sigma,
        )
        dark_response = cv2.subtract(background, blurred)
        _threshold, binary = cv2.threshold(
            dark_response,
            0,
            255,
            cv2.THRESH_BINARY + cv2.THRESH_OTSU,
        )

        open_kernel_size = _fit_odd_kernel_size(
            reference_radius * _OPEN_KERNEL_RADIUS_RATIO,
            min(binary.shape[:2]),
        )
        kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE,
            (open_kernel_size, open_kernel_size),
        )
        binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)

        contours, _hierarchy = cv2.findContours(
            binary,
            cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_SIMPLE,
        )
        candidates = []
        for contour in contours:
            candidate = _candidate_from_contour(
                contour,
                image_width=binary.shape[1],
                image_height=binary.shape[0],
                small_min_radius=small_min_radius,
                small_max_radius=small_max_radius,
                factor=factor,
                cv2=cv2,
            )
            if candidate is not None:
                candidates.append(candidate)

        used_global_otsu_fallback = False
        if not candidates:
            # 大面积亮端面包围中央深色圆时，局部背景差分容易优先
            # 提取端面外侧阴影。此时用全局反向 Otsu 保留深色目标，
            # 并使用 RETR_LIST 读取位于亮端面内部的嵌套圆轮廓。
            _fallback_threshold, fallback_binary = cv2.threshold(
                blurred,
                0,
                255,
                cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU,
            )
            fallback_kernel = cv2.getStructuringElement(
                cv2.MORPH_ELLIPSE,
                (5, 5),
            )
            fallback_binary = cv2.morphologyEx(
                fallback_binary,
                cv2.MORPH_OPEN,
                fallback_kernel,
            )
            fallback_contours, _fallback_hierarchy = cv2.findContours(
                fallback_binary,
                cv2.RETR_LIST,
                cv2.CHAIN_APPROX_SIMPLE,
            )
            for contour in fallback_contours:
                candidate = _candidate_from_contour(
                    contour,
                    image_width=fallback_binary.shape[1],
                    image_height=fallback_binary.shape[0],
                    small_min_radius=small_min_radius,
                    small_max_radius=small_max_radius,
                    factor=factor,
                    cv2=cv2,
                )
                if candidate is not None:
                    candidates.append(candidate)
            used_global_otsu_fallback = bool(candidates)

        warnings: list[str] = []
        if not candidates:
            return [], None, False, ["轮廓找圆未找到候选圆"]
        if used_global_otsu_fallback:
            warnings.append(
                "局部背景找圆无候选，已使用全局反向 Otsu 兜底"
            )

        candidates.sort(key=lambda item: item.score, reverse=True)
        raw_candidate_count = len(candidates)
        candidates = _deduplicate_candidates(
            candidates,
            min_center_distance_px=config.min_center_distance_px,
        )
        detected_count = len(candidates)
        if detected_count == 0:
            return [], None, False, ["轮廓找圆未产生有效候选圆"]

        if raw_candidate_count != detected_count:
            warnings.append(
                f"轮廓原始候选 {raw_candidate_count} 个，"
                f"按圆心距离去重后 {detected_count} 个"
            )

        if detected_count != config.expected_circle_count:
            warnings.append(
                f"预期检测到 {config.expected_circle_count} 个圆，"
                f"轮廓去重后检测到 {detected_count} 个"
            )

        # 候选在此之前按圆度从高到低排列，所以超出预期时只取高分圆。
        selected_candidates = candidates[:config.expected_circle_count]
        highest_score_candidate = selected_candidates[0]
        # 使用位置顺序生成稳定的 circle-001 等编号；selected_index 仍指向
        # 圆度最高的圆，保持现有单圆 GUI 和规则引擎兼容。
        selected_candidates.sort(
            key=lambda item: (item.center_x, item.center_y, item.radius_px)
        )
        selected_index = next(
            index
            for index, candidate in enumerate(selected_candidates)
            if candidate is highest_score_candidate
        )

        complete_count = len(selected_candidates) == config.expected_circle_count
        low_score_candidates = [
            candidate
            for candidate in selected_candidates
            if candidate.score < config.min_candidate_score
        ]
        confirmed = complete_count and not low_score_candidates
        for candidate in low_score_candidates:
            warnings.append(
                f"候选圆中心({candidate.center_x:.1f}, {candidate.center_y:.1f})"
                f"圆度评分 {candidate.score:.3f} 低于自动确认阈值"
                f" {config.min_candidate_score:.3f}"
            )

        return selected_candidates, selected_index, confirmed, warnings


class HoughCircleDetector(ContourCircleDetector):
    """旧类名兼容入口；内部已经不再调用 HoughCircles。"""


def _candidate_from_contour(
    contour,
    *,
    image_width: int,
    image_height: int,
    small_min_radius: float,
    small_max_radius: float,
    factor: int,
    cv2,
) -> Optional[CircleCandidate]:
    """将一个轮廓按面积、圆度、长宽比和边界条件转换为圆候选。"""

    area = float(cv2.contourArea(contour))
    perimeter = float(cv2.arcLength(contour, True))
    if area <= 0 or perimeter <= 0:
        return None

    x, y, width, height = cv2.boundingRect(contour)
    if (
        x <= 0
        or y <= 0
        or x + width >= image_width
        or y + height >= image_height
    ):
        # 贴边轮廓通常是画面边缘、阴影或未完整进入视野的端面。
        return None

    aspect_ratio = width / height
    if not _MIN_ASPECT_RATIO <= aspect_ratio <= _MAX_ASPECT_RATIO:
        return None

    equivalent_radius = math.sqrt(area / math.pi)
    if not small_min_radius <= equivalent_radius <= small_max_radius:
        return None

    circularity = 4.0 * math.pi * area / (perimeter * perimeter)
    if not math.isfinite(circularity) or circularity < _MIN_CIRCULARITY:
        return None

    moments = cv2.moments(contour)
    if moments["m00"] == 0:
        return None
    center_x = moments["m10"] / moments["m00"]
    center_y = moments["m01"] / moments["m00"]

    return CircleCandidate(
        center_x=float(center_x) * factor,
        center_y=float(center_y) * factor,
        radius_px=float(equivalent_radius) * factor,
        score=min(1.0, float(circularity)),
        source="contour",
    )


def _fit_odd_kernel_size(requested_size: float, image_limit: int) -> int:
    """生成不超过图像短边的正奇数形态学核尺寸。"""

    kernel_size = max(3, int(round(requested_size)))
    if kernel_size % 2 == 0:
        kernel_size += 1

    maximum = max(1, int(image_limit))
    if maximum % 2 == 0:
        maximum -= 1
    return max(1, min(kernel_size, maximum))


def _deduplicate_candidates(
    candidates: list[CircleCandidate],
    *,
    min_center_distance_px: float,
) -> list[CircleCandidate]:
    """保留高分候选；圆心距离小于配置阈值的候选视为同一端面。"""

    kept = []
    for candidate in candidates:
        duplicate = any(
            math.hypot(
                candidate.center_x - existing.center_x,
                candidate.center_y - existing.center_y,
            ) < min_center_distance_px
            for existing in kept
        )
        if not duplicate:
            kept.append(candidate)
    return kept


def _validate_detection_config(config: CircleDetectionConfig) -> list[str]:
    """只校验轮廓找圆运行时直接依赖的参数。"""

    errors = []
    if config.downsample_factor < 1:
        errors.append("找圆降采样倍数必须至少为 1")
    if config.blur_kernel_size < 1 or config.blur_kernel_size % 2 == 0:
        errors.append("找圆模糊核尺寸必须是正奇数")
    if (
        not math.isfinite(config.min_center_distance_px)
        or config.min_center_distance_px <= 0
    ):
        errors.append("候选圆心最小距离必须大于 0")
    if config.min_radius_px < 0:
        errors.append("候选圆最小半径不能小于 0")
    if config.max_radius_px <= config.min_radius_px:
        errors.append("候选圆最大半径必须大于最小半径")
    if config.expected_circle_count < 1:
        errors.append("预期圆数量必须至少为 1")
    if not math.isfinite(config.min_candidate_score) or not (
        0 <= config.min_candidate_score <= 1
    ):
        errors.append("候选圆最低评分必须在 0～1 之间")
    return errors


def _prepare_grayscale(image, cv2, np):
    """检查输入图像并转换成轮廓分析需要的 uint8 灰度图。"""

    if image is None:
        raise ValueError("轮廓找圆收到空图像")

    array = np.asarray(image)
    if array.size == 0:
        raise ValueError("轮廓找圆收到空图像")

    if array.ndim == 2:
        grayscale = array
    elif array.ndim == 3 and array.shape[2] == 1:
        grayscale = array[:, :, 0]
    elif array.ndim == 3 and array.shape[2] == 3:
        grayscale = cv2.cvtColor(array, cv2.COLOR_BGR2GRAY)
    elif array.ndim == 3 and array.shape[2] == 4:
        grayscale = cv2.cvtColor(array, cv2.COLOR_BGRA2GRAY)
    else:
        raise ValueError("轮廓找圆只支持灰度图、BGR 图或 BGRA 图")

    if grayscale.dtype != np.uint8:
        finite = np.isfinite(grayscale)
        if not finite.any():
            raise ValueError("轮廓找圆图像不包含有限像素")
        safe = np.where(finite, grayscale, 0)
        grayscale = cv2.normalize(
            safe,
            None,
            0,
            255,
            cv2.NORM_MINMAX,
        ).astype(np.uint8)

    return np.ascontiguousarray(grayscale)
