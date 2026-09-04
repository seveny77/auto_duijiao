# -*- coding: utf-8 -*-
"""在原图旁保存全分辨率的质检叠加图。"""

from pathlib import Path

import cv2

from backend.inspection_renderer import render_image_inspection_overlay


def save_inspection_image(image, result, config, original_image_path):
    """复用原图文件名，保存所有端面的缺陷、标签和质检圆环。"""

    original_path = Path(original_image_path)
    output_path = original_path.with_name(f"{original_path.stem}_inspection.jpg")
    overlay = render_image_inspection_overlay(
        image,
        result,
        config,
        background="original",
        show_contours=True,
        show_circle=True,
        show_rings=True,
        show_rois=True,
    )
    # 与自动保存原图相同的 JPEG 质量；tofile 支持 Windows 中文目录。
    succeeded, encoded = cv2.imencode(
        ".jpg", overlay, [cv2.IMWRITE_JPEG_QUALITY, 95]
    )
    if not succeeded:
        raise OSError("检测结果图 JPEG 编码失败")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    encoded.tofile(str(output_path))
    return str(output_path)
