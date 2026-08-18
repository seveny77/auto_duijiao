# -*- coding: utf-8 -*-
"""训练样本生成：D:\\scan\\1|2|3 全幅 Tenengrad 评价 -> 找 best -> 生成标签 -> 重命名

标签规则：label_um = (best_idx - idx) * 5        # μm，可为负/0/正
命名规则：idx{idx:04d}_delta{label:+d}um.jpg     # 保留原始编号 + 标签

输出：
  D:\\scan\\{folder}\\tenengrad_scores.csv   每文件夹全幅 T 曲线（index, z_um, t_score, is_best）
  D:\\scan\\dataset.csv                      汇总清单（含 旧名->新名 映射，可回溯）

用法：
  python prepare_dlfocus_dataset.py [--root D:\\scan] [--step-um 5]
"""

import argparse
import csv
import glob
import os
import time

import cv2
import numpy as np


def tenengrad(img):
    """全幅 Tenengrad：Sobel 梯度幅值均值（与生产评价器一致）。"""
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gx = cv2.Sobel(g, cv2.CV_64F, 1, 0)
    gy = cv2.Sobel(g, cv2.CV_64F, 0, 1)
    return float(np.mean(gx ** 2 + gy ** 2))


def label_name(idx, best, step_um):
    label = int(round((best - idx) * step_um))
    sign = "+" if label > 0 else ""
    return f"idx{idx:04d}_delta{sign}{label}um.jpg"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=r"D:\scan")
    ap.add_argument("--step-um", type=float, default=5.0)
    ap.add_argument("--folders", default="1,2,3")
    args = ap.parse_args()

    folders = [f.strip() for f in args.folders.split(",") if f.strip()]
    manifest = []
    total = 0

    for folder in folders:
        d = os.path.join(args.root, folder)
        paths = sorted(glob.glob(os.path.join(d, "*.jpg")))
        if not paths:
            print(f"[{folder}] 无图片，跳过")
            continue
        print(f"\n[{folder}] 图片数: {len(paths)}")

        # ---- 第一遍：全幅 Tenengrad 评价 ----
        t0 = time.perf_counter()
        scores = []
        for i, p in enumerate(paths):
            img = cv2.imread(p)
            if img is None:
                raise RuntimeError(f"无法读取: {p}")
            scores.append(tenengrad(img))
            if (i + 1) % 100 == 0:
                print(f"  评价 {i + 1}/{len(paths)} ({time.perf_counter() - t0:.0f}s)")
        scores = np.array(scores)
        best = int(np.argmax(scores))
        ratio = scores.max() / scores.min()
        print(f"[{folder}] best_idx={best}, T={scores[best]:.2f}, "
              f"T范围={scores.min():.1f}..{scores.max():.1f}, max/min={ratio:.2f}, "
              f"评价耗时={time.perf_counter() - t0:.0f}s")

        # ---- 保存 T 曲线 CSV ----
        score_csv = os.path.join(d, "tenengrad_scores.csv")
        with open(score_csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["index", "z_um", "t_score", "is_best"])
            for i, s in enumerate(scores):
                w.writerow([i, round(i * args.step_um, 1), round(s, 4), 1 if i == best else 0])
        print(f"[{folder}] T 曲线已保存: {score_csv}")

        # ---- 第二遍：重命名（先检查冲突，再逐个改名） ----
        plan = []
        for i, p in enumerate(paths):
            new_name = label_name(i, best, args.step_um)
            new_path = os.path.join(d, new_name)
            if new_path != p and os.path.exists(new_path):
                raise RuntimeError(f"目标已存在（中止，避免覆盖）: {new_path}")
            plan.append((p, new_path, new_name))
        for p, new_path, new_name in plan:
            if new_path != p:
                os.rename(p, new_path)
            manifest.append({
                "sample_id": folder,
                "idx": int(os.path.basename(new_name).split("_")[0][3:]),
                "z_um": round(int(os.path.basename(new_name).split("_")[0][3:]) * args.step_um, 1),
                "label_um": int(round((best - int(os.path.basename(new_name).split("_")[0][3:])) * args.step_um)),
                "best_idx": best,
                "old_name": os.path.basename(p),
                "new_name": new_name,
                "path": new_path,
            })
        total += len(plan)
        print(f"[{folder}] 重命名完成 {len(plan)} 张")

    # ---- 汇总清单 ----
    manifest_path = os.path.join(args.root, "dataset.csv")
    with open(manifest_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=[
            "sample_id", "idx", "z_um", "label_um", "best_idx",
            "old_name", "new_name", "path",
        ])
        w.writeheader()
        for row in manifest:
            w.writerow(row)
    print(f"\n总计 {total} 张，清单已保存: {manifest_path}")


if __name__ == "__main__":
    main()
