# -*- coding: utf-8 -*-
"""离线质检图片读取工具。"""

import os


def load_inspection_image(path):
    """将本地图片读取为 uint8 BGR 图像，并兼容 Windows 中文路径。"""

    try:
        normalized_path = os.fspath(path)
    except TypeError as error:
        raise ValueError("本地检测图片路径无效") from error
    if not isinstance(normalized_path, str) or not normalized_path.strip():
        raise ValueError("本地检测图片路径不能为空")

    # 延迟导入，避免仅启动 GUI 时由这个轻量工具提前加载 OpenCV。
    import cv2
    import numpy as np

    try:
        encoded = np.fromfile(normalized_path, dtype=np.uint8)
    except (OSError, ValueError) as error:
        raise ValueError(
            f"无法读取本地检测图片: {normalized_path}"
        ) from error
    if encoded.size == 0:
        raise ValueError(f"本地检测图片为空: {normalized_path}")

    try:
        image = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    except cv2.error as error:
        raise ValueError(
            f"本地检测图片解码失败: {normalized_path}"
        ) from error
    if image is None or image.size == 0:
        raise ValueError(
            f"本地检测图片格式无效或文件已损坏: {normalized_path}"
        )

    return np.ascontiguousarray(image)
