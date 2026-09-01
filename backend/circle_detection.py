# -*- coding: utf-8 -*-
"""使用 OpenCV Hough 圆检测定位最终成像中的产品圆。"""

import math
from typing import Optional

from backend.inspection_config import CircleDetectionConfig
from backend.inspection_types import CircleCandidate


class HoughCircleDetector:
    """在降采样图上检测、去重并选择配置数量的产品圆。"""

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
        small_min_distance = max(
            1.0,
            config.min_center_distance_px / factor,
        )
        small_min_radius = max(
            0,
            int(round(config.min_radius_px / factor)),
        )
        small_max_radius = max(
            small_min_radius + 1,
            int(round(config.max_radius_px / factor)),
        )

        raw_circles = cv2.HoughCircles(
            blurred,
            cv2.HOUGH_GRADIENT,
            dp=config.hough_dp,
            minDist=small_min_distance,
            param1=config.hough_param1,
            param2=config.hough_param2,
            minRadius=small_min_radius,
            maxRadius=small_max_radius,
        )

        warnings: list[str] = []
        if raw_circles is None or raw_circles.size == 0:
            return [], None, False, ["Hough 圆检测未找到候选圆"]

        edges = cv2.Canny(
            blurred,
            threshold1=max(1.0, config.hough_param1 * 0.5),
            threshold2=config.hough_param1,
        )
        candidates = []
        for center_x, center_y, radius in raw_circles[0]:
            if not all(math.isfinite(float(value)) for value in (
                center_x,
                center_y,
                radius,
            )):
                continue
            if radius <= 0:
                continue

            score = _circle_edge_support(
                edges,
                float(center_x),
                float(center_y),
                float(radius),
                np,
            )
            candidates.append(CircleCandidate(
                center_x=float(center_x) * factor,
                center_y=float(center_y) * factor,
                radius_px=float(radius) * factor,
                score=score,
                source="hough",
            ))

        candidates.sort(key=lambda item: item.score, reverse=True)
        raw_candidate_count = len(candidates)
        candidates = _deduplicate_candidates(
            candidates,
            min_center_distance_px=config.min_center_distance_px,
        )
        detected_count = len(candidates)
        if detected_count == 0:
            return [], None, False, ["Hough 圆检测未产生有效候选圆"]

        if raw_candidate_count != detected_count:
            warnings.append(
                f"Hough 原始候选 {raw_candidate_count} 个，"
                f"按圆心距离去重后 {detected_count} 个"
            )

        if detected_count != config.expected_circle_count:
            warnings.append(
                f"预期检测到 {config.expected_circle_count} 个圆，"
                f"Hough 去重后检测到 {detected_count} 个"
            )

        selected_candidates = candidates[:config.expected_circle_count]
        highest_score_candidate = selected_candidates[0]
        # 进入多 ROI 流程后使用这个位置顺序生成稳定的 circle-001 等编号；
        # selected_index 仍指向评分最高的圆，保持当前单圆 GUI/规则引擎兼容。
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
                f"评分 {candidate.score:.3f} 低于自动确认阈值"
                f" {config.min_candidate_score:.3f}"
            )

        return selected_candidates, selected_index, confirmed, warnings


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
    """只校验 Hough 检测直接依赖的参数。"""

    errors = []
    if config.downsample_factor < 1:
        errors.append("找圆降采样倍数必须至少为 1")
    if config.blur_kernel_size < 1 or config.blur_kernel_size % 2 == 0:
        errors.append("找圆模糊核尺寸必须是正奇数")
    for name, value in (
        ("Hough dp", config.hough_dp),
        ("Hough param1", config.hough_param1),
        ("Hough param2", config.hough_param2),
        ("候选圆心最小距离", config.min_center_distance_px),
    ):
        if not math.isfinite(value) or value <= 0:
            errors.append(f"{name} 必须大于 0")
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
    """检查输入图像并转换成 HoughCircles 需要的 uint8 灰度图。"""

    if image is None:
        raise ValueError("Hough 圆检测收到空图像")

    array = np.asarray(image)
    if array.size == 0:
        raise ValueError("Hough 圆检测收到空图像")

    if array.ndim == 2:
        grayscale = array
    elif array.ndim == 3 and array.shape[2] == 1:
        grayscale = array[:, :, 0]
    elif array.ndim == 3 and array.shape[2] == 3:
        grayscale = cv2.cvtColor(array, cv2.COLOR_BGR2GRAY)
    elif array.ndim == 3 and array.shape[2] == 4:
        grayscale = cv2.cvtColor(array, cv2.COLOR_BGRA2GRAY)
    else:
        raise ValueError(
            "Hough 圆检测只支持灰度图、BGR 图或 BGRA 图"
        )

    if grayscale.dtype != np.uint8:
        finite = np.isfinite(grayscale)
        if not finite.any():
            raise ValueError("Hough 圆检测图像不包含有限像素")
        safe = np.where(finite, grayscale, 0)
        grayscale = cv2.normalize(
            safe,
            None,
            0,
            255,
            cv2.NORM_MINMAX,
        ).astype(np.uint8)

    return np.ascontiguousarray(grayscale)


def _circle_edge_support(
    edges,
    center_x: float,
    center_y: float,
    radius: float,
    np,
) -> float:
    """计算候选圆周附近存在 Canny 边缘的采样点比例。"""

    sample_count = 360
    tolerance = 2
    angles = np.linspace(
        0.0,
        2.0 * math.pi,
        sample_count,
        endpoint=False,
    )
    x_values = np.rint(center_x + radius * np.cos(angles)).astype(int)
    y_values = np.rint(center_y + radius * np.sin(angles)).astype(int)

    hits = 0
    height, width = edges.shape[:2]
    for x_value, y_value in zip(x_values, y_values):
        left = max(0, x_value - tolerance)
        right = min(width, x_value + tolerance + 1)
        top = max(0, y_value - tolerance)
        bottom = min(height, y_value + tolerance + 1)
        if left < right and top < bottom and edges[top:bottom, left:right].any():
            hits += 1

    return hits / sample_count
