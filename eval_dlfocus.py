# -*- coding: utf-8 -*-
"""评估已训练的 ResNet18 单帧对焦模型（复用 train_dlfocus 的组件）

用法：
  python eval_dlfocus.py --model dlfocus_out/best_resnet.pt \
      --split dlfocus_out/split_primary.csv
  python eval_dlfocus.py --model dlfocus_out/best_resnet.pt --data D:\\scan\\dataset.csv
"""

import argparse
import csv
import json
import os
import time

import numpy as np
import torch

from train_dlfocus import (
    build_resnet, predict, compute_metrics, measure_inference_ms,
    save_plots, load_rows, split_primary, IMG_SIZE,
)


def read_split_rows(split_csv):
    with open(split_csv, newline="", encoding="utf-8-sig") as f:
        rows = [r for r in csv.DictReader(f) if r["split"] == "test"]
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=os.path.join("dlfocus_out", "best_resnet.pt"))
    ap.add_argument("--split", default=os.path.join("dlfocus_out", "split_primary.csv"))
    ap.add_argument("--data", default=None, help="不传 split 时按主划分重新切分")
    ap.add_argument("--out", default="dlfocus_out")
    ap.add_argument("--device", default="auto")
    ap.add_argument("--tag", default="eval")
    args = ap.parse_args()

    device = torch.device(
        "cuda" if torch.cuda.is_available() else "cpu"
        if args.device == "auto" else args.device
    )
    if args.split and os.path.exists(args.split):
        rows = read_split_rows(args.split)
    else:
        rows = split_primary(load_rows(args.data))[2]

    label_scale = None
    res_path = os.path.join(args.out, "results.json")
    if os.path.exists(res_path):
        with open(res_path, encoding="utf-8") as f:
            label_scale = json.load(f).get("label_scale")

    model = build_resnet().to(device)
    model.load_state_dict(torch.load(args.model, map_location=device))
    model.eval()

    preds, labels = predict(model, rows, device, label_scale=label_scale)
    metrics = compute_metrics(preds, labels)
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    os.makedirs(args.out, exist_ok=True)
    save_plots(preds, labels, metrics, args.out, args.tag)
    with open(os.path.join(args.out, f"eval_{args.tag}.json"), "w", encoding="utf-8") as f:
        json.dump({"metrics": metrics, "n": len(rows)}, f, ensure_ascii=False, indent=2)

    if device.type == "cuda":
        ms = measure_inference_ms(model, device)
        print(f"GPU 推理耗时: {ms:.2f} ms/帧 (目标 <20ms)")


if __name__ == "__main__":
    main()
