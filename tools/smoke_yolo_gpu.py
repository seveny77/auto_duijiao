# -*- coding: utf-8 -*-
"""在独立进程中验证 CUDA、找圆模型和分割模型的真实推理。"""

import argparse
import json
import os
import sys
import time
from pathlib import Path


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="验证工控机 YOLO GPU 推理环境")
    parser.add_argument("--segmentation-model", required=True)
    parser.add_argument("--circle-model", required=True)
    parser.add_argument("--device", default="0")
    parser.add_argument("--imgsz", type=int, default=1024)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    segmentation_path = Path(args.segmentation_model).resolve()
    circle_path = Path(args.circle_model).resolve()
    for label, path in (
        ("分割模型", segmentation_path),
        ("找圆模型", circle_path),
    ):
        if not path.is_file():
            raise FileNotFoundError(f"{label}不存在: {path}")
    if args.imgsz < 32:
        raise ValueError("imgsz 必须至少为 32")

    # 必须在导入服务及 Ultralytics 前设置，确保本脚本实际走指定设备。
    os.environ["AUTOFOCUS_YOLO_DEVICE"] = str(args.device)

    import numpy as np
    import torch

    if not torch.cuda.is_available():
        raise RuntimeError(
            "PyTorch 未检测到 CUDA；请检查 CUDA wheel 和 NVIDIA 驱动"
        )
    device_index = int(str(args.device).split(":")[-1])
    torch.cuda.set_device(device_index)
    tensor = torch.ones((256, 256), device=f"cuda:{device_index}")
    torch.mm(tensor, tensor)
    torch.cuda.synchronize(device_index)

    from backend.circle_model_service import CircleModelService
    from backend.segmentation_model_service import SegmentationModelService
    from ultralytics.utils.events import events

    image = np.zeros((args.imgsz, args.imgsz, 3), dtype=np.uint8)
    circle_service = CircleModelService()
    circle_start = time.perf_counter()
    circle_service.load(str(circle_path), confidence_floor=0.01)
    circle_service.predict_circles(
        image,
        expected_count=1,
        confidence_floor=0.01,
    )
    circle_ms = (time.perf_counter() - circle_start) * 1000.0

    segmentation_service = SegmentationModelService()
    segmentation_start = time.perf_counter()
    segmentation_service.load(
        str(segmentation_path),
        imgsz=args.imgsz,
        confidence_floor=0.01,
    )
    segmentation_service.predict(
        image,
        imgsz=args.imgsz,
        confidence_floor=0.01,
    )
    segmentation_ms = (time.perf_counter() - segmentation_start) * 1000.0

    report = {
        "passed": True,
        "python": sys.version.split()[0],
        "torch": torch.__version__,
        "torch_cuda": torch.version.cuda,
        "gpu": torch.cuda.get_device_name(device_index),
        "device": str(args.device),
        "ultralytics_events_enabled": bool(events.enabled),
        "circle_load_and_predict_ms": round(circle_ms, 2),
        "segmentation_load_and_predict_ms": round(segmentation_ms, 2),
    }
    if events.enabled:
        raise RuntimeError("Ultralytics 联网统计仍处于启用状态")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
