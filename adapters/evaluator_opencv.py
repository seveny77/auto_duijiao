# adapters/evaluator_opencv.py
# 职责：用 OpenCV 模拟 SDK 的 scImageSharpness，提供统一的 evaluate() 接口

import cv2
import numpy as np


class OpenCVSharpnessEvaluator:
    """
    离线验证用的清晰度评价器。
    接口设计刻意模仿 scImageSharpness：
      - evaluate(image_path, roi) → float
    将来换成 SDK 时，只需换一个类，接口不变。
    """

    def __init__(self):
        self.last_score = 0.0   # 最近一次评价的分数，方便调试时查看

    def evaluate(self, image_path: str, roi: tuple = None) -> float:
        """
        输入:
            image_path: 图片文件路径
            roi: (x, y, w, h) 或 None（全图）
        输出:
            清晰度分数，越高越清晰
        """
        # 1. 读图
        img_array = np.fromfile(image_path, dtype=np.uint8)
        img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        if img is None:
            raise FileNotFoundError(f"无法读取图片: {image_path}")

        # 2. 裁 ROI
        if roi is not None:
            x, y, w, h = roi
            patch = img[y:y+h, x:x+w]
        else:
            patch = img

        # 3. 转灰度
        if patch.ndim == 3:
            gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
        else:
            gray = patch

        # 4. Tenengrad — Sobel 梯度幅值均值，抗局部高对比度干扰
        gx = cv2.Sobel(gray, cv2.CV_64F, 1, 0)
        gy = cv2.Sobel(gray, cv2.CV_64F, 0, 1)
        self.last_score = float(np.mean(gx**2 + gy**2))

        return self.last_score

    def evaluate_image(self, img: np.ndarray, roi: tuple = None) -> float:
        """输入已在内存中的图像数组，避免反复读盘"""
        if roi is not None:
            x, y, w, h = roi
            patch = img[y:y + h, x:x + w]
        else:
            patch = img

        if patch.ndim == 3:
            gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
        else:
            gray = patch

        # Tenengrad
        gx = cv2.Sobel(gray, cv2.CV_64F, 1, 0)
        gy = cv2.Sobel(gray, cv2.CV_64F, 0, 1)
        self.last_score = float(np.mean(gx**2 + gy**2))
        return self.last_score
