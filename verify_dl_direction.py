# -*- coding: utf-8 -*-
"""Phase 0：单帧离焦方向可分性验证（AI 模型验证）

背景
----
固定场景对焦计划用"单帧图 -> 模型直接回归 Δz（离最佳焦点的偏移）"替代粗扫+精扫。
方案成立的前提是：距最佳焦点 ±d 的两张图存在可被模型学习的差异（方向可分）。
本脚本用一份"全扫"数据（index 与 Z 位置单调对应）验证这个前提。

验证内容
--------
1. 图像层统计：对每个 |d| 比较 best+d 与 best-d 两图的差异
   （MAE / NCC / 均值差符号一致性），并与随机图对基线对比；
2. AI 方向二分类：小型 CNN 输入单帧 ROI patch，预测方向（+/-），
   按 d 分组划分训练/测试（默认训练偶数 d、测试奇数 d），
   报告整体与逐 d 准确率（基线 50%）；
3. （可选 --regress）有符号偏移回归：同一骨干输出 Δz（index），
   报告 MAE（index/μm）与方向错误率。

用法示例
--------
  # 直接用 T 方法全扫找 best（中心 ROI），再做全部验证
  python verify_dl_direction.py --images D:\\scan5um\\0807 --roi 2386,1474,700,700

  # 用已有 ROI 全扫 CSV 找 best（推荐，快）
  python verify_dl_direction.py --images D:\\scan5um\\0807 --scores features_z_out\\0807_roi700_t.csv

  # 快速冒烟测试
  python verify_dl_direction.py --images D:\\scan5um\\0807 --scores features_z_out\\0807_roi700_t.csv \
      --limit 30 --patches-per-img 4 --epochs 2

输出（--out-dir，默认 features_z_out/）
  phase0_symmetry.csv        # 各 d 的图像差异统计
  phase0_report.txt          # 文本报告
  phase0_direction.png       # 统计 + 方向准确率曲线
"""

import argparse
import csv
import glob
import os
import random
import time

import cv2
import numpy as np

HAS_TORCH = True
try:
    import torch
    import torch.nn as nn
    from torch.utils.data import Dataset, DataLoader
except Exception:
    HAS_TORCH = False

HAS_MPL = True
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    for _font in ("Microsoft YaHei", "SimHei", "SimSun", "Noto Sans CJK SC"):
        try:
            plt.rcParams["font.sans-serif"] = [_font]
            plt.rcParams["axes.unicode_minus"] = False
            break
        except Exception:
            continue
except Exception:
    HAS_MPL = False


# ============================================================
# 1. 数据加载 / 找 best
# ============================================================

def load_image(path):
    return cv2.imread(path)


def tenengrad(img, roi=None):
    """与生产评价器一致的 Tenengrad（ROI 内 Sobel 梯度幅值均值）。"""
    if roi:
        x, y, w, h = roi
        p = img[y:y + h, x:x + w]
    else:
        p = img
    g = cv2.cvtColor(p, cv2.COLOR_BGR2GRAY)
    gx = cv2.Sobel(g, cv2.CV_64F, 1, 0)
    gy = cv2.Sobel(g, cv2.CV_64F, 0, 1)
    return float(np.mean(gx ** 2 + gy ** 2))


def collect_image_paths(images_dir):
    paths = []
    for ext in ("*.jpg", "*.jpeg", "*.png", "*.bmp"):
        paths.extend(glob.glob(os.path.join(images_dir, ext)))
    paths = sorted(paths)
    if not paths:
        raise FileNotFoundError(f"目录中没有图片: {images_dir}")
    return paths


def find_best_index(paths, scores_csv=None, score_col="t", roi=None,
                    score_stride=1, step_um=5.0):
    """返回 (best_index, best_score, scores 全列表或 None)。

    优先读已有 CSV；否则用 T 方法全扫（可 --score-stride 抽稀后局部加密）。
    """
    if scores_csv:
        with open(scores_csv, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        if not rows:
            raise ValueError(f"CSV 为空: {scores_csv}")
        vals = [float(r[score_col]) for r in rows]
        best = int(np.argmax(vals))
        print(f"[scores CSV] best index = {best} "
              f"({score_col}={vals[best]:.4f}, n={len(vals)}, 对应 z={best * step_um:.0f} um)")
        return best, vals[best], vals

    print("[T 全扫] 正在逐帧评价找 best ...")
    scores = []
    for i, p in enumerate(paths):
        img = load_image(p)
        scores.append(tenengrad(img, roi))
        if (i + 1) % 50 == 0:
            print(f"  {i + 1}/{len(paths)}")
    scores = np.array(scores)
    if score_stride > 1:
        coarse_idx = np.arange(0, len(scores), score_stride)
        k = int(coarse_idx[np.argmax(scores[coarse_idx])])
        lo, hi = max(0, k - score_stride), min(len(scores) - 1, k + score_stride)
        best = int(lo + np.argmax(scores[lo:hi + 1]))
    else:
        best = int(np.argmax(scores))
    print(f"[T 全扫] best index = {best} (score={scores[best]:.4f})")
    return best, float(scores[best]), scores.tolist()


# ============================================================
# 2. 图像层对称性统计
# ============================================================

def _roi_gray(img, roi, size=256):
    if roi:
        x, y, w, h = roi
        img = img[y:y + h, x:x + w]
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return cv2.resize(g, (size, size), interpolation=cv2.INTER_AREA)


def pair_stats(img_a, img_b, roi):
    """返回 (mae, ncc, mean_diff)。mean_diff = mean(b) - mean(a)。"""
    a = _roi_gray(img_a, roi).astype(np.float32)
    b = _roi_gray(img_b, roi).astype(np.float32)
    mae = float(np.mean(np.abs(a - b)))
    ncc = float(np.corrcoef(a.ravel(), b.ravel())[0, 1])
    return mae, ncc, float(np.mean(b) - np.mean(a))


def random_pair_baseline(paths, roi, n_pairs=20, seed=0):
    rng = random.Random(seed)
    vals = []
    for _ in range(n_pairs):
        i, j = rng.sample(range(len(paths)), 2)
        a = load_image(paths[i])
        b = load_image(paths[j])
        vals.append(pair_stats(a, b, roi))
    return np.mean([v[0] for v in vals]), np.mean([v[1] for v in vals])


# ============================================================
# 3. Patch 数据集（方向二分类 / 偏移回归）
# ============================================================

class PatchOffsetDataset(Dataset):
    """从 best±d 图像中抽取 ROI patch。

    每个 (d, side) 图像抽取 patches_per_img 个随机 224x224 裁剪。
    label: 分类任务为 ±1（+d 侧为 +1）；回归任务为有符号 d（index）。
    """

    def __init__(self, paths, best, ds, roi, patch_size=224,
                 patches_per_img=8, seed=0, region_size=512):
        self.paths = paths
        self.best = best
        self.roi = roi
        self.patch_size = patch_size
        self.ppi = patches_per_img
        self.region_size = region_size
        self.rng = random.Random(seed)
        # (idx, side) side: +1 表示 best+d, -1 表示 best-d
        self.items = []
        for d in ds:
            if 0 <= best + d < len(paths):
                self.items.append((best + d, 1, d))
            if 0 <= best - d < len(paths):
                self.items.append((best - d, -1, d))
        self._cache = {}

    def __len__(self):
        return len(self.items) * self.ppi

    def _region(self, idx):
        if idx not in self._cache:
            img = load_image(self.paths[idx])
            if self.roi:
                x, y, w, h = self.roi
                img = img[y:y + h, x:x + w]
            g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            # 缓存前缩小，避免全幅图撑爆内存（patch 在缩小后的区域上随机裁剪）
            hh, ww = g.shape
            if max(hh, ww) > self.region_size:
                scale = self.region_size / max(hh, ww)
                g = cv2.resize(g, (max(1, int(ww * scale)), max(1, int(hh * scale))),
                               interpolation=cv2.INTER_AREA)
            self._cache[idx] = g
        return self._cache[idx]

    def __getitem__(self, i):
        item_idx, side, d = self.items[i // self.ppi]
        reg = self._region(item_idx)
        h, w = reg.shape
        ps = self.patch_size
        if h < ps or w < ps:
            reg = cv2.resize(reg, (max(w, ps), max(h, ps)))
            h, w = reg.shape
        y0 = self.rng.randint(0, h - ps)
        x0 = self.rng.randint(0, w - ps)
        patch = reg[y0:y0 + ps, x0:x0 + ps].astype(np.float32) / 255.0
        patch = torch.from_numpy(patch).unsqueeze(0)  # 1 x H x W
        cls_label = torch.tensor(1.0 if side > 0 else 0.0)
        reg_label = torch.tensor(float(side * d))
        return patch, cls_label, reg_label


# ============================================================
# 4. 小型 CNN
# ============================================================

class SmallCNN(nn.Module):
    """约 0.5M 参数的轻量 CNN：方向二分类 / 偏移回归共用骨干。"""

    def __init__(self):
        super().__init__()
        self.features = nn.Sequential(
            nn.Conv2d(1, 32, 3, padding=1), nn.BatchNorm2d(32), nn.ReLU(), nn.MaxPool2d(2),
            nn.Conv2d(32, 64, 3, padding=1), nn.BatchNorm2d(64), nn.ReLU(), nn.MaxPool2d(2),
            nn.Conv2d(64, 128, 3, padding=1), nn.BatchNorm2d(128), nn.ReLU(), nn.MaxPool2d(2),
        )
        self.head = nn.Sequential(
            nn.AdaptiveAvgPool2d(1), nn.Flatten(), nn.Linear(128, 64), nn.ReLU(), nn.Linear(64, 1),
        )

    def forward(self, x):
        return self.head(self.features(x))


def train_loop(model, loader, task, epochs, device, lr=1e-3):
    if task == "cls":
        criterion = nn.BCEWithLogitsLoss()
    else:
        criterion = nn.SmoothL1Loss()
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    model.train()
    for ep in range(epochs):
        tot, n = 0.0, 0
        for patches, cls_y, reg_y in loader:
            patches = patches.to(device)
            out = model(patches).squeeze(1)
            if task == "cls":
                loss = criterion(out, cls_y.to(device))
                acc = ((out > 0).float() == cls_y.to(device)).float().mean().item()
                tot += loss.item()
                n += 1
            else:
                loss = criterion(out, reg_y.to(device))
                tot += loss.item()
                n += 1
            opt.zero_grad()
            loss.backward()
            opt.step()
        if task == "cls" and n:
            print(f"  epoch {ep + 1}/{epochs}  loss={tot / n:.4f}")
        elif n:
            print(f"  epoch {ep + 1}/{epochs}  loss={tot / n:.4f}")


def evaluate(model, dataset, task, device, batch_size=64, step_um=5.0):
    loader = DataLoader(dataset, batch_size=batch_size, shuffle=False)
    model.eval()
    correct = 0
    total = 0
    mae_idx = 0.0
    dir_err = 0
    with torch.no_grad():
        for patches, cls_y, reg_y in loader:
            out = model(patches.to(device)).squeeze(1).cpu()
            if task == "cls":
                pred = (out > 0).float()
                correct += (pred == cls_y).sum().item()
                total += cls_y.numel()
            else:
                err = (out - reg_y).abs()
                mae_idx += err.sum().item()
                total += reg_y.numel()
                dir_err += (torch.sign(out) != torch.sign(reg_y)).sum().item()
    if task == "cls":
        return correct / total
    return mae_idx / total, dir_err / total


def per_d_accuracy(model, paths, best, test_ds, roi, device, patches_per_img=4,
                   patch_size=224, seed=0):
    """逐 d 的方向准确率（每个 d 单独评估，多 patch 投票）。"""
    model.eval()
    result = {}
    for d in sorted(test_ds):
        ds = PatchOffsetDataset(paths, best, [d], roi, patch_size,
                                patches_per_img=patches_per_img, seed=seed)
        if len(ds) == 0:
            continue
        correct = 0
        votes_total = 0
        loader = DataLoader(ds, batch_size=32, shuffle=False)
        with torch.no_grad():
            for patches, cls_y, _ in loader:
                out = model(patches.to(device)).squeeze(1).cpu()
                correct += ((out > 0).float() == cls_y).sum().item()
                votes_total += cls_y.numel()
        acc = correct / votes_total if votes_total else 0.0
        result[d] = acc
    return result


# ============================================================
# 5. 主流程
# ============================================================

def parse_args():
    ap = argparse.ArgumentParser(description="Phase 0 方向可分性验证")
    ap.add_argument("--images", required=True, help="全扫图片目录（index=Z 顺序）")
    ap.add_argument("--scores", default=None, help="全扫分数 CSV（列名为 index 与分数列）")
    ap.add_argument("--score-col", default="t", help="CSV 分数列名（默认 t）")
    ap.add_argument("--roi", default=None, help="ROI x,y,w,h（默认全图；0807 建议 2386,1474,700,700）")
    ap.add_argument("--step-um", type=float, default=5.0, help="index 对应的 μm 步距（默认 5）")
    ap.add_argument("--max-d", type=int, default=40, help="最大 |d|（index）")
    ap.add_argument("--limit", type=int, default=0, help="只在 [best-limit, best+limit] 内取数据（冒烟测试用）")
    ap.add_argument("--patch-size", type=int, default=224)
    ap.add_argument("--patches-per-img", type=int, default=8)
    ap.add_argument("--epochs", type=int, default=15)
    ap.add_argument("--batch-size", type=int, default=64)
    ap.add_argument("--train-ds", default=None, help="训练 d 列表，逗号分隔；默认所有偶数 d（测试为奇数 d）")
    ap.add_argument("--regress", action="store_true", help="同时训练有符号偏移回归")
    ap.add_argument("--score-stride", type=int, default=1, help="无 CSV 时 T 全扫抽稀步长")
    ap.add_argument("--device", default="auto")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out-dir", default="features_z_out")
    return ap.parse_args()


def main():
    args = parse_args()
    random.seed(args.seed)
    np.random.seed(args.seed)
    if HAS_TORCH:
        torch.manual_seed(args.seed)

    if args.device == "auto":
        device = "cuda" if HAS_TORCH and torch.cuda.is_available() else "cpu"
    else:
        device = args.device
    print(f"device: {device}, torch: {HAS_TORCH}, matplotlib: {HAS_MPL}")

    roi = tuple(int(v) for v in args.roi.split(",")) if args.roi else None
    if roi:
        print(f"ROI: {roi}")

    paths = collect_image_paths(args.images)
    print(f"图片数: {len(paths)}")

    t0 = time.perf_counter()
    best, best_score, _ = find_best_index(
        paths, args.scores, args.score_col, roi, args.score_stride, args.step_um
    )
    print(f"找 best 耗时: {time.perf_counter() - t0:.1f} s")

    if args.limit > 0:
        lo = max(0, best - args.limit)
        hi = min(len(paths) - 1, best + args.limit)
        paths = paths[lo:hi + 1]
        best = best - lo
        print(f"[limit] 数据范围收缩到 [{lo}, {hi}]，best 相对 index = {best}")

    max_d = min(args.max_d, best, len(paths) - 1 - best)
    all_ds = list(range(1, max_d + 1))
    if not all_ds:
        raise SystemExit("无可用的 d 范围（best 太贴边），请扩大行程或检查 best")

    os.makedirs(args.out_dir, exist_ok=True)
    report_path = os.path.join(args.out_dir, "phase0_report.txt")
    report = open(report_path, "w", encoding="utf-8")

    def log(msg, also_print=True):
        report.write(str(msg) + "\n")
        if also_print:
            print(msg)

    log(f"=== Phase 0 方向可分性验证报告 ===")
    log(f"图片目录: {args.images}")
    log(f"best index: {best}, max_d: {max_d}, step: {args.step_um} um")
    log(f"roi: {roi}, device: {device}")

    # ---------- 2.1 图像层统计 ----------
    log("\n--- 2.1 图像层对称性统计 ---")
    base_mae, base_ncc = random_pair_baseline(paths, roi, seed=args.seed)
    log(f"随机图对基线: MAE={base_mae:.2f}, NCC={base_ncc:.4f}")

    sym_rows = []
    for d in all_ds:
        pa, pb = paths[best + d], paths[best - d]
        mae, ncc, md = pair_stats(load_image(pa), load_image(pb), roi)
        sym_rows.append((d, mae, ncc, md))
    log(f"{'d':>3} {'MAE':>8} {'NCC':>8} {'mean_diff':>10}")
    for d, mae, ncc, md in sym_rows:
        log(f"{d:>3} {mae:8.2f} {ncc:8.4f} {md:10.3f}")
    with open(os.path.join(args.out_dir, "phase0_symmetry.csv"), "w",
              newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["d", "mae", "ncc", "mean_diff", "baseline_mae", "baseline_ncc"])
        for d, mae, ncc, md in sym_rows:
            w.writerow([d, round(mae, 3), round(ncc, 4), round(md, 3),
                        round(base_mae, 3), round(base_ncc, 4)])

    if not HAS_TORCH:
        log("\n[警告] 未安装 torch，跳过 AI 模型验证。")
        report.close()
        return

    # ---------- 2.2 方向二分类 ----------
    if args.train_ds:
        train_ds = [int(x) for x in args.train_ds.split(",")]
        test_ds = [d for d in all_ds if d not in train_ds]
    else:
        train_ds = [d for d in all_ds if d % 2 == 0]
        test_ds = [d for d in all_ds if d % 2 == 1]
    if not train_ds or not test_ds:
        raise SystemExit("训练/测试 d 集合不能为空，请检查 --train-ds 或 --max-d")
    log(f"\n--- 2.2 方向二分类 (train d={train_ds[:5]}..., test d={test_ds[:5]}...) ---")

    train_set = PatchOffsetDataset(paths, best, train_ds, roi, args.patch_size,
                                   args.patches_per_img, args.seed)
    test_set = PatchOffsetDataset(paths, best, test_ds, roi, args.patch_size,
                                  args.patches_per_img, args.seed)
    log(f"训练 patch 数: {len(train_set)}, 测试 patch 数: {len(test_set)}")

    model = SmallCNN().to(device)
    train_loader = DataLoader(train_set, batch_size=args.batch_size, shuffle=True)
    train_loop(model, train_loader, "cls", args.epochs, device)
    acc = evaluate(model, test_set, "cls", device, args.batch_size)
    log(f"方向二分类测试准确率: {acc * 100:.1f}% (随机基线 50.0%)")

    per_d = per_d_accuracy(model, paths, best, test_ds, roi, device,
                           patches_per_img=min(4, args.patches_per_img),
                           patch_size=args.patch_size, seed=args.seed)
    log(f"逐 d 方向准确率（测试 d）:")
    for d in sorted(per_d):
        log(f"  d={d:>3} ({d * args.step_um:5.1f} um): {per_d[d] * 100:5.1f}%")

    # ---------- 2.3 有符号偏移回归（可选） ----------
    if args.regress:
        log("\n--- 2.3 有符号偏移回归 ---")
        reg_model = SmallCNN().to(device)
        reg_loader = DataLoader(train_set, batch_size=args.batch_size, shuffle=True)
        train_loop(reg_model, reg_loader, "reg", args.epochs, device)
        mae_idx, dir_err = evaluate(reg_model, test_set, "reg", device,
                                    args.batch_size, args.step_um)
        log(f"回归 MAE: {mae_idx:.2f} index = {mae_idx * args.step_um:.1f} um")
        log(f"方向错误率: {dir_err * 100:.1f}%")

    # ---------- 2.4 绘图 ----------
    if HAS_MPL:
        fig, axes = plt.subplots(1, 2, figsize=(13, 4.5))
        ds = [r[0] for r in sym_rows]
        axes[0].plot(ds, [r[1] for r in sym_rows], "o-", label="MAE (best±d)")
        axes[0].axhline(base_mae, color="gray", ls="--", label=f"随机对基线 MAE={base_mae:.2f}")
        axes[0].set_xlabel("|d| (index)")
        axes[0].set_ylabel("MAE")
        axes[0].set_title("图像层差异 vs 随机基线")
        axes[0].legend()
        axes[1].axhline(0.5, color="red", ls="--", label="随机 50%")
        if per_d:
            axes[1].plot(sorted(per_d), [per_d[d] for d in sorted(per_d)], "s-",
                         label="方向准确率 (test d)")
        axes[1].set_xlabel("|d| (index)")
        axes[1].set_ylabel("准确率")
        axes[1].set_ylim(0, 1.05)
        axes[1].set_title("单帧方向二分类")
        axes[1].legend()
        png_path = os.path.join(args.out_dir, "phase0_direction.png")
        fig.tight_layout()
        fig.savefig(png_path, dpi=130)
        log(f"\n图表已保存: {png_path}")

    # ---------- 结论 ----------
    if acc >= 0.8:
        verdict = "方向可分性结论: 强（准确率 >= 80%），建议进入 Phase 1"
    elif acc >= 0.6:
        verdict = "方向可分性结论: 弱（60%~80%），需增大数据/增强或重新评估"
    else:
        verdict = "方向可分性结论: 不可分（≈50%），单帧回归方向不可学，建议改两帧方案"
    log(f"\n=== {verdict} ===")
    report.close()
    print(f"\n报告已保存: {report_path}")


if __name__ == "__main__":
    main()
