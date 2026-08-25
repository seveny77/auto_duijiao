# -*- coding: utf-8 -*-
"""AI单帧对焦模型的运行期加载和推理。"""

import json
import math
import os

import cv2
import numpy as np
import torch
import torch.nn as nn
from torchvision.models import resnet18


IMAGE_SIZE = 224

IMAGE_NET_MEAN = np.array(
    [0.485, 0.456, 0.406],
    dtype=np.float32,
).reshape(3, 1, 1)

IMAGE_NET_STD = np.array(
    [0.229, 0.224, 0.225],
    dtype=np.float32,
).reshape(3, 1, 1)


def build_focus_resnet():
    """构造与训练时完全相同的ResNet18回归网络。"""

    # 正式运行时不需要下载ImageNet预训练权重。
    #
    # 后面会完整加载best_resnet.pt中的参数，
    # 因此这里只需要创建相同的网络结构。
    model = resnet18(weights=None)

    model.fc = nn.Sequential(
        nn.Linear(model.fc.in_features, 128),
        nn.ReLU(inplace=True),
        nn.Linear(128, 1),
    )

    return model


def to_model_input(image_bgr):
    """把OpenCV的BGR图像转换成模型输入张量。"""

    if image_bgr is None:
        raise ValueError("AI模型输入图像为空")

    if not isinstance(image_bgr, np.ndarray):
        raise TypeError(
            "AI模型输入必须是NumPy图像，"
            f"实际类型为 {type(image_bgr).__name__}"
        )

    if image_bgr.ndim != 3 or image_bgr.shape[2] != 3:
        raise ValueError(
            "AI模型输入必须是BGR三通道图像，"
            f"实际shape={image_bgr.shape}"
        )

    # 训练时使用的预处理顺序必须完整保留：
    #
    # BGR三通道图像
    # → 灰度图
    # → 224×224
    # → 转为0～1浮点数
    # → 复制为三通道
    gray = cv2.cvtColor(
        image_bgr,
        cv2.COLOR_BGR2GRAY,
    )

    gray = cv2.resize(
        gray,
        (IMAGE_SIZE, IMAGE_SIZE),
        interpolation=cv2.INTER_AREA,
    )

    image = gray.astype(np.float32) / 255.0

    tensor = torch.from_numpy(image)
    tensor = tensor.unsqueeze(0).repeat(3, 1, 1)

    mean = torch.from_numpy(IMAGE_NET_MEAN)
    std = torch.from_numpy(IMAGE_NET_STD)

    return (tensor - mean) / std


class DLDistanceModel:
    """加载单帧对焦回归模型，并预测有符号焦点位移。"""

    def __init__(
        self,
        model_path: str,
        label_scale: float = None,
        device=None,
    ):
        self.model_path = os.path.abspath(model_path)

        if not os.path.isfile(self.model_path):
            raise FileNotFoundError(
                f"AI对焦模型不存在: {self.model_path}"
            )

        self.label_scale = (
            float(label_scale)
            if label_scale is not None
            else self._load_label_scale()
        )

        if self.label_scale <= 0:
            raise ValueError(
                "AI模型label_scale必须大于0，"
                f"实际值为 {self.label_scale}"
            )

        self.device = torch.device(
            device
            if device is not None
            else (
                "cuda"
                if torch.cuda.is_available()
                else "cpu"
            )
        )

        self.model = build_focus_resnet()
        self.model.to(self.device)

        state_dict = torch.load(
            self.model_path,
            map_location=self.device,
            weights_only=True,
        )

        self.model.load_state_dict(
            state_dict,
            strict=True,
        )

        self.model.eval()

    def _load_label_scale(self) -> float:
        """从模型同目录的results.json读取label_scale。"""

        results_path = os.path.join(
            os.path.dirname(self.model_path),
            "results.json",
        )

        if not os.path.isfile(results_path):
            raise FileNotFoundError(
                "没有找到AI模型对应的results.json: "
                f"{results_path}"
            )

        with open(
            results_path,
            "r",
            encoding="utf-8",
        ) as file:
            results = json.load(file)

        if "label_scale" not in results:
            raise KeyError(
                "results.json中缺少label_scale"
            )

        return float(results["label_scale"])

    def predict_frame(self, image_bgr) -> float:
        """根据一张相机图像预测焦点相对位移，单位为µm。"""

        tensor = to_model_input(image_bgr)
        tensor = tensor.unsqueeze(0)
        tensor = tensor.to(
            self.device,
            non_blocking=True,
        )

        with torch.inference_mode():
            output = self.model(tensor)

        normalized_delta = float(
            output.reshape(-1)[0].item()
        )

        delta_z_um = (
            normalized_delta
            * self.label_scale
        )

        if not math.isfinite(delta_z_um):
            raise RuntimeError(
                "AI模型输出不是有效数字: "
                f"{delta_z_um}"
            )

        return delta_z_um

    def warmup(
        self,
        width: int = 1368,
        height: int = 912,
    ) -> float:
        """使用空白图执行一次预热推理。"""

        dummy_image = np.zeros(
            (height, width, 3),
            dtype=np.uint8,
        )

        return self.predict_frame(
            dummy_image
        )