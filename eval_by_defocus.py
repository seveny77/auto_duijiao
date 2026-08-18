# -*- coding: utf-8 -*-
"""按离焦距离（|Δz|）分区间统计预测偏差。

用法：
  python eval_by_defocus.py
  python eval_by_defocus.py --bins "0,50,100,200,300,500,800,1200,2000"

输出（--out，默认 dlfocus_out/）：
  by_defocus_test.csv   测试集（120 张）分区间统计
  by_defocus_all.csv    全量（1200 张，含训练图，参考）
  by_defocus_mae.png    MAE 柱状图
"""

import argparse
import csv
import json
import os

import numpy as np
import torch

from train_dlfocus import (
    build_resnet, predict, compute_metrics, load_rows, filter_rows,
)

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    for _f in ("Microsoft YaHei", "SimHei", "SimSun"):
        try:
            plt.rcParams["font.sans-serif"] = [_f]
            plt.rcParams["axes.unicode_minus"] = False
            break
        except Exception:
            continue
    HAS_MPL = True
except Exception:
    HAS_MPL = False


def bin_stats(preds, labels, edges):
    preds = np.asarray(preds, dtype=float)
    labels = np.asarray(labels, dtype=float)
    err = preds - labels
    abs_l = np.abs(labels)
    rows = []
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        m = (abs_l >= lo) & (abs_l < hi)
        if not m.any():
            continue
        e = err[m]
        dir_mask = m & (np.abs(labels) >= 1)
        dir_err = (float(np.mean(np.sign(preds[dir_mask]) != np.sign(labels[dir_mask])) * 100)
                   if dir_mask.any() else float("nan"))
        rows.append({
            "bin_um": f"{lo}~{hi}",
            "n": int(m.sum()),
            "mae_um": float(np.mean(np.abs(e))),
            "rmse_um": float(np.sqrt(np.mean(e ** 2))),
            "mean_err_um": float(np.mean(e)),   # 有符号均值，>0 表示系统性偏大
            "hit5_pct": float(np.mean(np.abs(e) <= 5) * 100),
            "hit10_pct": float(np.mean(np.abs(e) <= 10) * 100),
            "hit15_pct": float(np.mean(np.abs(e) <= 15) * 100),
            "hit20_pct": float(np.mean(np.abs(e) <= 20) * 100),
            "hit25_pct": float(np.mean(np.abs(e) <= 25) * 100),
            "hit30_pct": float(np.mean(np.abs(e) <= 30) * 100),
            "hit50_pct": float(np.mean(np.abs(e) <= 50) * 100),
            "hit70_pct": float(np.mean(np.abs(e) <= 70) * 100),
            "hit90_pct": float(np.mean(np.abs(e) <= 90) * 100),
            "dir_err_pct": dir_err,
        })
    return rows


def print_table(rows, title):
    print(f"\n=== {title} ===")
    print(f"{'区间(μm)':<12}{'n':>5}{'MAE':>7}"
          f"{'±5':>6}{'±10':>6}{'±15':>6}{'±20':>6}{'±25':>6}{'±30':>6}{'方向错%':>8}")
    for r in rows:
        print(f"{r['bin_um']:<12}{r['n']:>5}{r['mae_um']:>7.1f}"
              f"{r['hit5_pct']:>6.1f}{r['hit10_pct']:>6.1f}{r['hit15_pct']:>6.1f}"
              f"{r['hit20_pct']:>6.1f}{r['hit25_pct']:>6.1f}{r['hit30_pct']:>6.1f}{r['dir_err_pct']:>8.1f}")


def overall_stats(preds, labels):
    preds = np.asarray(preds, dtype=float)
    labels = np.asarray(labels, dtype=float)
    err = np.abs(preds - labels)
    m = np.abs(labels) >= 1
    dir_err = (float(np.mean(np.sign(preds[m]) != np.sign(labels[m])) * 100)
               if m.any() else float("nan"))
    return {
        "mae_um": float(np.mean(err)),
        "hit5_pct": float(np.mean(err <= 5) * 100),
        "hit10_pct": float(np.mean(err <= 10) * 100),
        "hit15_pct": float(np.mean(err <= 15) * 100),
        "hit20_pct": float(np.mean(err <= 20) * 100),
        "hit25_pct": float(np.mean(err <= 25) * 100),
        "hit30_pct": float(np.mean(err <= 30) * 100),
        "dir_err_pct": dir_err,
    }


def save_csv(rows, path):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=os.path.join("dlfocus_out", "best_resnet.pt"))
    ap.add_argument("--data", default=r"D:\scan\dataset.csv")
    ap.add_argument("--split", default=os.path.join("dlfocus_out", "split_primary.csv"))
    ap.add_argument("--out", default="dlfocus_out")
    ap.add_argument("--bins", default="0,50,100,200,300,500,800,1200,2000")
    ap.add_argument("--device", default="auto")
    args = ap.parse_args()

    device = torch.device(
        "cuda" if torch.cuda.is_available() else "cpu"
        if args.device == "auto" else args.device
    )
    edges = [int(x) for x in args.bins.split(",")]
    edges[-1] = float("inf")

    label_scale = None
    max_abs_label_um = 0.0
    res_path = os.path.join(args.out, "results.json")
    if os.path.exists(res_path):
        with open(res_path, encoding="utf-8") as f:
            data = json.load(f)
        label_scale = data.get("label_scale")
        max_abs_label_um = float(data.get("config", {}).get("max_abs_label_um", 0.0))

    all_rows = load_rows(args.data)
    all_rows, _ = filter_rows(all_rows, max_abs_label_um)
    test_rows = []
    with open(args.split, newline="", encoding="utf-8-sig") as f:
        test_rows = [r for r in csv.DictReader(f) if r["split"] == "test"]

    model = build_resnet().to(device)
    model.load_state_dict(torch.load(args.model, map_location=device))
    model.eval()

    for rows, tag, title in (
        (test_rows, "test", f"测试集分区间统计（{len(test_rows)} 张，诚实评估）"),
        (all_rows, "all", f"全量分区间统计（{len(all_rows)} 张，含训练图，参考）"),
    ):
        preds, labels = predict(model, rows, device, label_scale=label_scale)
        stats = bin_stats(preds, labels, edges)
        print_table(stats, title)
        ov = overall_stats(preds, labels)
        print(f"整体({len(preds)}张): MAE={ov['mae_um']:.1f}  "
              f"±5={ov['hit5_pct']:.1f}% ±10={ov['hit10_pct']:.1f}% "
              f"±15={ov['hit15_pct']:.1f}% ±20={ov['hit20_pct']:.1f}% "
              f"±25={ov['hit25_pct']:.1f}% ±30={ov['hit30_pct']:.1f}%  "
              f"方向错={ov['dir_err_pct']:.1f}%")
        save_csv(stats, os.path.join(args.out, f"by_defocus_{tag}.csv"))
        if HAS_MPL and tag == "test":
            names = [r["bin_um"] for r in stats]
            maes = [r["mae_um"] for r in stats]
            fig, ax = plt.subplots(figsize=(8, 4.5))
            ax.bar(names, maes)
            ax.axhline(15, color="red", ls="--", label="验收线 15 μm")
            ax.set_ylabel("MAE (μm)")
            ax.set_title("各离焦区间 MAE（测试集）")
            ax.legend()
            fig.tight_layout()
            fig.savefig(os.path.join(args.out, "by_defocus_mae.png"), dpi=130)
            plt.close(fig)
    print(f"\nCSV 已保存: {os.path.join(args.out, 'by_defocus_test.csv')}, "
          f"{os.path.join(args.out, 'by_defocus_all.csv')}")


if __name__ == "__main__":
    main()
