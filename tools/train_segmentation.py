# -*- coding: utf-8 -*-
"""Ultralytics YOLO-Seg 训练入口；本脚本不会被 GUI 自动调用。"""

import argparse
from pathlib import Path


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="训练最终成像缺陷 YOLO-Seg 模型")
    parser.add_argument("--data", required=True, help="YOLO-Seg data.yaml")
    parser.add_argument("--base-model", default="yolo11n-seg.pt")
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--imgsz", type=int, default=1280)
    parser.add_argument("--batch", type=int, default=4)
    parser.add_argument("--device", default="0")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--project", default="yoloSegRuns")
    parser.add_argument("--name", default="defect_seg_v1")
    parser.add_argument("--patience", type=int, default=30)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--cache", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    return parser.parse_args(argv)


def build_train_arguments(args):
    """校验参数并构造传给 Ultralytics 的关键字参数。"""

    data_path = Path(args.data).resolve()
    if not data_path.is_file():
        raise FileNotFoundError(f"data.yaml 不存在: {data_path}")
    if args.epochs < 1:
        raise ValueError("epochs 必须大于 0")
    if args.imgsz < 1:
        raise ValueError("imgsz 必须大于 0")
    if args.batch == 0 or args.batch < -1:
        raise ValueError("batch 必须为 -1 或大于 0")
    if args.workers < 0:
        raise ValueError("workers 不能小于 0")
    if args.patience < 0:
        raise ValueError("patience 不能小于 0")

    epochs = 1 if args.smoke else args.epochs
    workers = 0 if args.smoke else args.workers
    batch = min(2, args.batch) if args.smoke and args.batch > 0 else args.batch
    return {
        "data": str(data_path),
        "epochs": epochs,
        "imgsz": args.imgsz,
        "batch": batch,
        "device": args.device,
        "workers": workers,
        "project": str(Path(args.project).resolve()),
        "name": args.name,
        "patience": args.patience,
        "seed": args.seed,
        "cache": args.cache,
        "resume": args.resume,
    }


def train(args):
    """显式执行训练；导入模块或查看 --help 不会加载模型/CUDA。"""

    train_arguments = build_train_arguments(args)
    from ultralytics import YOLO
    model = YOLO(args.base_model)
    if str(getattr(model, "task", "segment")).lower() != "segment":
        raise ValueError("基础模型不是 YOLO-Seg 分割模型")
    return model.train(**train_arguments)


def main(argv=None):
    args = parse_args(argv)
    train(args)


if __name__ == "__main__":
    main()
