# -*- coding: utf-8 -*-
"""清晰度评价 ROI 的纯 Python 数据校验和边界适配。"""

from typing import Optional, Tuple


EvaluationRoi = Tuple[int, int, int, int]


def normalize_evaluation_roi(value) -> Optional[EvaluationRoi]:
    """把 JSON/GUI 输入规范化为 ``(x, y, width, height)``。

    ``None`` 表示尚未设置，等第一张图像到达后使用整张图像。
    布尔值虽然是 Python 的 int 子类，但在这里明确拒绝。
    """

    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError("清晰度 ROI 必须包含 x、y、宽度和高度")
    if any(isinstance(item, bool) or not isinstance(item, int) for item in value):
        raise ValueError("清晰度 ROI 的 x、y、宽度和高度必须是整数")

    x, y, width, height = (int(item) for item in value)
    if x < 0 or y < 0:
        raise ValueError("清晰度 ROI 的 x、y 不能小于 0")
    if width <= 0 or height <= 0:
        raise ValueError("清晰度 ROI 的宽度和高度必须大于 0")
    return x, y, width, height


def full_frame_roi(image_width: int, image_height: int) -> EvaluationRoi:
    """返回整张硬件 ROI 图像对应的局部 ROI。"""

    if image_width <= 0 or image_height <= 0:
        raise ValueError("图像宽度和高度必须大于 0")
    return 0, 0, int(image_width), int(image_height)


def evaluation_roi_fits_image(
    roi: EvaluationRoi,
    image_width: int,
    image_height: int,
) -> bool:
    """判断局部 ROI 是否完整位于当前图像内部。"""

    x, y, width, height = roi
    return (
        image_width > 0
        and image_height > 0
        and x >= 0
        and y >= 0
        and width > 0
        and height > 0
        and x + width <= image_width
        and y + height <= image_height
    )


def fit_evaluation_roi(
    value,
    image_width: int,
    image_height: int,
) -> EvaluationRoi:
    """返回可用于当前图像的 ROI；缺失或越界时回退到整图。"""

    normalized = normalize_evaluation_roi(value)
    if normalized is None or not evaluation_roi_fits_image(
        normalized,
        image_width,
        image_height,
    ):
        return full_frame_roi(image_width, image_height)
    return normalized
