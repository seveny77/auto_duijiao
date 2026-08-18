# -*- coding: utf-8 -*-
"""ResNet18 单帧对焦回归：训练 + 双轨验证 + MLP 基线 + 指标输出

数据：D:\\scan\\dataset.csv（1200 张，3 样本 × 400，label_um = (best_idx - idx) * 5）
输入：全幅 1024x748 -> 灰度 -> 224x224 -> 3 通道 + ImageNet 归一化
输出：有符号 Δz（μm）

用法：
  python train_dlfocus.py --smoke                 # 冒烟：1 epoch, batch 16
  python train_dlfocus.py                         # 完整训练（主划分 + 3 折 LOO + MLP 基线）
  python train_dlfocus.py --skip-loo --skip-baseline --epochs 30
  python train_dlfocus.py --beta 0.02 --label-scale 0

标签归一化：LABEL_SCALE = max(|label_um|)（默认从全部数据自动计算，当前 1525 μm），
模型输出归一化 Δz，predict()/evaluate_mae() 乘回 scale 后所有指标为 μm。

产出（dlfocus_out/）：
  split_primary.csv        主划分（train/val/test）
  best_resnet.pt/.onnx     最优 ResNet18 权重 + ONNX
  train_log.txt            训练日志
  results.json             全部指标
  scatter.png / error_hist.png / buckets.png
"""

import argparse
import csv
import json
import os
import random
import re
import time

import cv2
import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader
from torchvision.models import resnet18, ResNet18_Weights

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

IMG_SIZE = 224
MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32).reshape(3, 1, 1)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32).reshape(3, 1, 1)

_PREPROC_CACHE: dict = {}
_PREPROC_DECODED = 0


# ============================================================
# 数据加载
# ============================================================

def load_rows(data_csv):
    with open(data_csv, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def filter_rows(rows, max_abs_label_um):
    """只保留 |label_um| <= max_abs_label_um 的行（>0 时生效）。返回 (过滤后行, 丢弃数)。"""
    if max_abs_label_um <= 0:
        return rows, 0
    kept = [r for r in rows if abs(float(r["label_um"])) <= max_abs_label_um]
    return kept, len(rows) - len(kept)


def load_image(path):
    arr = np.fromfile(path, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(path)
    return img


def to_model_input(img):
    """BGR -> 灰度 -> 224x224(INTER_AREA) -> [0,1] 张量(3,224,224)。"""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, (IMG_SIZE, IMG_SIZE), interpolation=cv2.INTER_AREA)
    x = gray.astype(np.float32) / 255.0
    x = torch.from_numpy(x).unsqueeze(0).repeat(3, 1, 1)  # (3,H,W)
    return x


def preprocess(path):
    """带缓存的预处理：解码一次，返回 (3,224,224) uint8 张量。"""
    global _PREPROC_DECODED
    t = _PREPROC_CACHE.get(path)
    if t is None:
        img = load_image(path)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, (IMG_SIZE, IMG_SIZE), interpolation=cv2.INTER_AREA)
        t = torch.from_numpy(gray).unsqueeze(0).repeat(3, 1, 1).to(torch.uint8)
        _PREPROC_CACHE[path] = t
        _PREPROC_DECODED += 1
    return t


def load_preproc_cache(path):
    """从磁盘加载预处理缓存；成功返回 True。"""
    global _PREPROC_CACHE
    if path and os.path.exists(path):
        try:
            data = torch.load(path, map_location="cpu")
            if isinstance(data, dict):
                _PREPROC_CACHE = data
                return True
        except Exception:
            return False
    return False


def save_preproc_cache(path):
    if path:
        torch.save(_PREPROC_CACHE, path)


def normalize(x):
    mean = torch.as_tensor(MEAN, device=x.device)
    std = torch.as_tensor(STD, device=x.device)
    return (x - mean) / std


class FocusDataset(Dataset):
    def __init__(self, rows, augment=False, label_scale=1.0):
        self.rows = rows
        self.augment = augment
        self.label_scale = label_scale

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, i):
        r = self.rows[i]
        x = preprocess(r["path"]).float() / 255.0
        if self.augment:
            b = random.uniform(0.9, 1.1)
            c = random.uniform(0.9, 1.1)
            x = torch.clamp(x * b, 0.0, 1.0)
            x = torch.clamp((x - 0.5) * c + 0.5, 0.0, 1.0)
            if random.random() < 0.5:
                x = torch.clamp(x + torch.randn_like(x) * (2.0 / 255.0), 0.0, 1.0)
        x = normalize(x)
        y = torch.tensor(float(r["label_um"]) / self.label_scale)
        return x, y
"""
def predict(model, rows, device, batch=64, label_scale=None):
    if label_scale is None:
        label_scale = max(abs(float(r["label_um"])) for r in rows)
    ds = FocusDataset(rows, label_scale=label_scale)
    loader = DataLoader(ds, batch_size=batch, shuffle=False)
    preds, labels = [], []
    model.eval()
    with torch.no_grad():
        for x, y in loader:
            x = x.to(device)
            if device.type == "cuda":
                with torch.autocast("cuda"):
                    out = model(x).squeeze(1).float()
            else:
                out = model(x).squeeze(1)
            preds.extend((out.cpu().numpy() * label_scale).tolist())
            labels.extend((y.numpy() * label_scale).tolist())
    return np.asarray(preds), np.asarray(labels)
"""
class DLDistanceModel:      # 名字你定，比如 DLVisionModel / DLVisionModel
    def __init__(self, model_path, label_scale=None):
        self.model_path = model_path
        self.label_scale = label_scale
        if label_scale is None:
            self.label_scale = json.load(open(os.path.join(os.path.dirname(model_path), "results.json"), encoding="utf-8"))["label_scale"]
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.model = build_resnet().to(self.device)
        self.model.load_state_dict(torch.load(model_path, map_location=self.device))
        self.model.eval()

    def predict_frame(self, img_bgr) -> float:   # 返回 Δz（μm）
        x = to_model_input(img_bgr)
        x = normalize(x).unsqueeze(0)
        with torch.no_grad():
            x = x.to(self.device)
            out = self.model(x).squeeze(1).float()
        return float(out.item() * self.label_scale)


def split_primary(rows, seed=0, ratios=(0.8, 0.1, 0.1)):
    """按样本分层随机 80/10/10，返回 (train, val, test) 行列表。"""
    rng = random.Random(seed)
    by_sample = {}
    for r in rows:
        by_sample.setdefault(r["sample_id"], []).append(r)
    train, val, test = [], [], []
    for sid, sub in by_sample.items():
        rng.shuffle(sub)
        n = len(sub)
        n_tr = int(round(n * ratios[0]))
        n_va = int(round(n * ratios[1]))
        train += sub[:n_tr]
        val += sub[n_tr:n_tr + n_va]
        test += sub[n_tr + n_va:]
    return train, val, test


def loo_folds(rows, seed=0):
    """留一样本 3 折：(train, val, test_rows, held_sample)。val 为 train 中随机 10%。"""
    rng = random.Random(seed)
    folds = []
    for held in sorted({r["sample_id"] for r in rows}):
        train_all = [r for r in rows if r["sample_id"] != held]
        test = [r for r in rows if r["sample_id"] == held]
        rng.shuffle(train_all)
        n_val = max(1, int(round(len(train_all) * 0.1)))
        val, train = train_all[:n_val], train_all[n_val:]
        folds.append((train, val, test, held))
    return folds


def parse_loo_from_log(log_path):
    """从训练日志解析最近一次运行的 LOO 折结果与均值。"""
    if not os.path.exists(log_path):
        return None
    with open(log_path, encoding="utf-8") as f:
        text = f.read()
    folds = []
    for m in re.finditer(
        r"fold sample(\d+): mae=([\d.]+) hit10=([\d.]+)% dir_err=([\d.]+)%", text
    ):
        folds.append({
            "held_sample": m.group(1),
            "mae_um": float(m.group(2)),
            "hit10_pct": float(m.group(3)),
            "dir_err_pct": float(m.group(4)),
        })
    mean = None
    m = re.search(r"LOO 平均: (\{.*\})", text)
    if m:
        mean = json.loads(m.group(1))
    early = None
    m = re.search(r"early stop @ ep (\d+), best_val_mae=([\d.]+)", text)
    if m:
        early = {"early_stop_epoch": int(m.group(1)), "best_val_mae_um": float(m.group(2))}
    if not folds:
        return None
    return {"folds": folds[-3:], "mean": mean, "primary_early_stop": early}


# ============================================================
# 模型
# ============================================================

def build_resnet():
    model = resnet18(weights=ResNet18_Weights.IMAGENET1K_V1)
    model.fc = nn.Sequential(
        nn.Linear(model.fc.in_features, 128),
        nn.ReLU(inplace=True),
        nn.Linear(128, 1),
    )
    return model


def param_groups(model, lr_backbone, lr_head, wd):
    head_ids = {id(p) for n, p in model.named_parameters() if n.startswith("fc.")}
    bb = [p for n, p in model.named_parameters() if id(p) not in head_ids]
    hd = [p for n, p in model.named_parameters() if id(p) in head_ids]
    return [
        {"params": bb, "lr": lr_backbone, "weight_decay": wd},
        {"params": hd, "lr": lr_head, "weight_decay": wd},
    ]


class MLPBaseline(nn.Module):
    def __init__(self, in_dim=3):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(in_dim, 64), nn.ReLU(),
            nn.Linear(64, 32), nn.ReLU(),
            nn.Linear(32, 1),
        )

    def forward(self, x):
        return self.net(x).squeeze(1)


# ============================================================
# 特征（MLP 基线）
# ============================================================

def compute_features(rows):
    feats = np.zeros((len(rows), 3), dtype=np.float32)
    for i, r in enumerate(rows):
        g = cv2.cvtColor(load_image(r["path"]), cv2.COLOR_BGR2GRAY)  # uint8，兼容 Laplacian
        gx = cv2.Sobel(g, cv2.CV_64F, 1, 0)
        gy = cv2.Sobel(g, cv2.CV_64F, 0, 1)
        ten = float(np.mean(gx ** 2 + gy ** 2))
        lap = float(cv2.Laplacian(g, cv2.CV_64F).var())
        var = float(g.astype(np.float32).var())
        feats[i] = (ten, lap, var)
    return feats


class FeatDataset(Dataset):
    def __init__(self, X, y):
        self.X = torch.from_numpy(X.astype(np.float32))
        self.y = torch.from_numpy(y.astype(np.float32))

    def __len__(self):
        return len(self.y)

    def __getitem__(self, i):
        return self.X[i], self.y[i]


# ============================================================
# 训练
# ============================================================

def train_model(model, train_ds, val_ds, device, cfg, tag, log):
    train_loader = DataLoader(train_ds, batch_size=cfg["batch"], shuffle=True)
    val_loader = DataLoader(val_ds, batch_size=cfg["batch"], shuffle=False)
    optimizer = torch.optim.AdamW(
        param_groups(model, cfg["lr_backbone"], cfg["lr_head"], cfg["wd"]),
    )
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
        optimizer, T_max=cfg["epochs"], eta_min=1e-5
    )
    criterion = nn.SmoothL1Loss(beta=cfg["beta"])
    scaler = torch.amp.GradScaler("cuda") if device.type == "cuda" else None
    best_mae = float("inf")
    best_state = None
    patience = 0
    history = []

    for ep in range(1, cfg["epochs"] + 1):
        model.train()
        tot_loss, n_batch = 0.0, 0
        t0 = time.perf_counter()
        for x, y in train_loader:
            x, y = x.to(device), y.to(device)
            optimizer.zero_grad()
            if scaler is not None:
                with torch.autocast("cuda"):
                    out = model(x).squeeze(1)
                    loss = criterion(out, y)
                scaler.scale(loss).backward()
                scaler.step(optimizer)
                scaler.update()
            else:
                out = model(x).squeeze(1)
                loss = criterion(out, y)
                loss.backward()
                optimizer.step()
            tot_loss += loss.item()
            n_batch += 1
        scheduler.step()

        # 验证
        val_mae, val_rmse = evaluate_mae(model, val_loader, device, cfg["label_scale"])
        history.append({"epoch": ep, "train_loss": tot_loss / n_batch,
                        "val_mae": val_mae, "val_rmse": val_rmse,
                        "lr": optimizer.param_groups[0]["lr"]})
        line = (f"[{tag}] ep {ep}/{cfg['epochs']} loss={tot_loss / n_batch:.3f} "
                f"val_mae={val_mae:.2f}um ({time.perf_counter() - t0:.0f}s)")
        log(line)
        if val_mae < best_mae:
            best_mae = val_mae
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
            patience = 0
        else:
            patience += 1
            if patience >= cfg["patience"]:
                log(f"[{tag}] early stop @ ep {ep}, best_val_mae={best_mae:.2f}")
                break
    model.load_state_dict(best_state)
    return model, best_mae, history


def evaluate_mae(model, loader, device, label_scale=1.0):
    errs = []
    model.eval()
    with torch.no_grad():
        for x, y in loader:
            x = x.to(device)
            if device.type == "cuda":
                with torch.autocast("cuda"):
                    out = model(x).float()
            else:
                out = model(x)
            out = out.squeeze(1) if out.dim() > 1 else out
            errs.append((out.cpu().numpy() - y.numpy()) * label_scale)
    err = np.concatenate(errs)
    return float(np.mean(np.abs(err))), float(np.sqrt(np.mean(err ** 2)))


def predict(model, rows, device, batch=64, label_scale=None):
    if label_scale is None:
        label_scale = max(abs(float(r["label_um"])) for r in rows)
    ds = FocusDataset(rows, label_scale=label_scale)
    loader = DataLoader(ds, batch_size=batch, shuffle=False)
    preds, labels = [], []
    model.eval()
    with torch.no_grad():
        for x, y in loader:
            x = x.to(device)
            if device.type == "cuda":
                with torch.autocast("cuda"):
                    out = model(x).squeeze(1).float()
            else:
                out = model(x).squeeze(1)
            preds.extend((out.cpu().numpy() * label_scale).tolist())
            labels.extend((y.numpy() * label_scale).tolist())
    return np.asarray(preds), np.asarray(labels)


def compute_metrics(preds, labels):
    preds = np.asarray(preds, dtype=float)
    labels = np.asarray(labels, dtype=float)
    err = preds - labels
    m = np.abs(labels) >= 1
    dir_err = float(np.mean(np.sign(preds[m]) != np.sign(labels[m])) * 100) if m.any() else float("nan")
    buckets = {}
    for lo, hi, name in [(0, 50, "lt50"), (50, 200, "50to200"), (200, 1e12, "gt200")]:
        b = (np.abs(labels) >= lo) & (np.abs(labels) < hi)
        if b.any():
            bm = b & m
            buckets[name] = {
                "n": int(b.sum()),
                "mae_um": float(np.mean(np.abs(err[b]))),
                "dir_err_pct": float(np.mean(np.sign(preds[bm]) != np.sign(labels[bm])) * 100) if bm.any() else float("nan"),
            }
    return {
        "n": int(len(labels)),
        "mae_um": float(np.mean(np.abs(err))),
        "rmse_um": float(np.sqrt(np.mean(err ** 2))),
        "hit15_pct": float(np.mean(np.abs(err) <= 15) * 100),
        "hit5_pct": float(np.mean(np.abs(err) <= 5) * 100),
        "hit10_pct": float(np.mean(np.abs(err) <= 10) * 100),
        "dir_err_pct": dir_err,
        "buckets": buckets,
    }


def train_mlp(Xtr, ytr, Xva, yva, Xte, yte, device, cfg, log, label_scale=1.0):
    ds_tr = FeatDataset(Xtr, ytr)
    ds_va = FeatDataset(Xva, yva)
    ds_te = FeatDataset(Xte, yte)
    model = MLPBaseline(Xtr.shape[1]).to(device)
    opt = torch.optim.Adam(model.parameters(), lr=1e-3, weight_decay=1e-4)
    crit = nn.SmoothL1Loss(beta=cfg["beta"])
    loader = DataLoader(ds_tr, batch_size=cfg["batch"], shuffle=True)
    best_mae, best_state, patience = float("inf"), None, 0
    for ep in range(1, cfg["epochs"] + 1):
        model.train()
        for x, y in loader:
            x, y = x.to(device), y.to(device)
            opt.zero_grad()
            loss = crit(model(x), y)
            loss.backward()
            opt.step()
        va_mae, _ = evaluate_mae(model, DataLoader(ds_va, batch_size=256), device, label_scale)
        if va_mae < best_mae:
            best_mae = va_mae
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
            patience = 0
        else:
            patience += 1
            if patience >= cfg["patience"]:
                break
    model.load_state_dict(best_state)
    preds, labels = [], []
    model.eval()
    with torch.no_grad():
        for x, y in DataLoader(ds_te, batch_size=256):
            preds.extend((model(x.to(device)).cpu().numpy() * label_scale).tolist())
            labels.extend((y.numpy() * label_scale).tolist())
    log(f"[mlp-baseline] best_val_mae={best_mae:.2f}um")
    return compute_metrics(preds, labels)


# ============================================================
# 推理耗时
# ============================================================

def measure_inference_ms(model, device, n=100, warmup=10):
    x = torch.randn(1, 3, IMG_SIZE, IMG_SIZE, device=device)
    model.eval()
    with torch.no_grad():
        for _ in range(warmup):
            model(x)
        t0 = time.perf_counter()
        for _ in range(n):
            model(x)
        dt = (time.perf_counter() - t0) / n * 1000
    return dt


# ============================================================
# 绘图 / 结果
# ============================================================

def save_plots(preds, labels, metrics, out_dir, tag):
    if not HAS_MPL:
        return
    err = np.asarray(preds) - np.asarray(labels)
    # 散点
    fig, ax = plt.subplots(figsize=(6, 6))
    ax.scatter(labels, preds, s=8, alpha=0.4)
    lim = [min(labels.min(), preds.min()) - 20, max(labels.max(), preds.max()) + 20]
    ax.plot(lim, lim, "r--", lw=1)
    ax.set_xlabel("标签 Δz (μm)"); ax.set_ylabel("预测 Δz (μm)")
    ax.set_title(f"{tag} 预测 vs 标签 (MAE={metrics['mae_um']:.2f} μm)")
    fig.tight_layout(); fig.savefig(os.path.join(out_dir, f"scatter_{tag}.png"), dpi=130)
    plt.close(fig)
    # 误差直方图
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.hist(err, bins=60, range=(-150, 150))
    ax.axvline(0, color="red", ls="--")
    ax.set_xlabel("预测误差 (μm)"); ax.set_ylabel("数量")
    ax.set_title(f"{tag} 误差分布 (RMSE={metrics['rmse_um']:.2f} μm)")
    fig.tight_layout(); fig.savefig(os.path.join(out_dir, f"error_hist_{tag}.png"), dpi=130)
    plt.close(fig)
    # 分档柱状
    if metrics.get("buckets"):
        names = list(metrics["buckets"].keys())
        maes = [metrics["buckets"][k]["mae_um"] for k in names]
        fig, ax = plt.subplots(figsize=(6, 4))
        ax.bar(names, maes)
        ax.set_ylabel("MAE (μm)"); ax.set_title(f"{tag} 分档 MAE")
        for i, v in enumerate(maes):
            ax.text(i, v + 1, f"{v:.1f}", ha="center")
        fig.tight_layout(); fig.savefig(os.path.join(out_dir, f"buckets_{tag}.png"), dpi=130)
        plt.close(fig)


def run_finish(args, device, log, cfg):
    """finish 模式：复用已保存的 best_resnet.pt 与训练日志，
    只补 MLP 基线 + 汇总 results.json（不重训）。"""
    rows = load_rows(args.data)
    rows, n_drop = filter_rows(rows, args.max_abs_label_um)
    if n_drop:
        log(f"过滤 |Δz| > {args.max_abs_label_um:.0f} μm: 丢弃 {n_drop} 张，保留 {len(rows)} 张")
    label_scale = args.label_scale if args.label_scale > 0 else max(
        abs(float(r["label_um"])) for r in rows
    )
    cfg["label_scale"] = label_scale
    train_r, val_r, test_r = split_primary(rows, seed=args.seed)
    results = {
        "config": cfg,
        "device": str(device),
        "label_scale": label_scale,
        "note": "finish 模式：主模型/LOO 来自已完成的训练，MLP 基线本次补算",
    }

    # MLP 基线（CPU 训练，避免与 CUDA 争内存）
    log("\n[MLP 基线]")
    all_rows = train_r + val_r + test_r
    feats_all = compute_features(all_rows)
    row2idx = {id(r): i for i, r in enumerate(all_rows)}
    Xtr = feats_all[[row2idx[id(r)] for r in train_r]]
    Xva = feats_all[[row2idx[id(r)] for r in val_r]]
    Xte = feats_all[[row2idx[id(r)] for r in test_r]]
    mu, sd = Xtr.mean(0), Xtr.std(0) + 1e-8
    Xtr, Xva, Xte = (Xtr - mu) / sd, (Xva - mu) / sd, (Xte - mu) / sd
    ytr = np.array([float(r["label_um"]) for r in train_r]) / label_scale
    yva = np.array([float(r["label_um"]) for r in val_r]) / label_scale
    yte = np.array([float(r["label_um"]) for r in test_r]) / label_scale
    base = train_mlp(Xtr, ytr, Xva, yva, Xte, yte, torch.device("cpu"),
                     cfg, log, label_scale)
    results["baseline_mlp"] = base
    log(f"[mlp-baseline] test: {json.dumps(base, ensure_ascii=False)}")

    # 主模型：复用已保存权重评估
    log("\n[主模型复用评估]")
    model = build_resnet().to(device)
    model.load_state_dict(torch.load(
        os.path.join(args.out, "best_resnet.pt"), map_location=device))
    preds, labels = predict(model, test_r, device, label_scale=label_scale)
    prim = compute_metrics(preds, labels)
    results["primary"] = prim
    log(f"[resnet-primary] test: {json.dumps(prim, ensure_ascii=False)}")
    if device.type == "cuda":
        ms = measure_inference_ms(model, device)
        results["inference_ms_gpu"] = ms
        log(f"GPU 推理耗时: {ms:.2f} ms/帧")

    # LOO 汇总（从日志解析最近一次运行）
    loo = parse_loo_from_log(os.path.join(args.out, "train_log.txt"))
    if loo:
        early = loo.pop("primary_early_stop", None)
        results["loo"] = loo
        results["primary_history"] = early
        log(f"LOO（日志解析）: {json.dumps(loo, ensure_ascii=False)}")

    with open(os.path.join(args.out, "results.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    log(f"\n完成（finish）。结果: {os.path.join(args.out, 'results.json')}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=r"D:\scan\dataset.csv")
    ap.add_argument("--out", default="dlfocus_out")
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--lr-backbone", type=float, default=1e-4)
    ap.add_argument("--lr-head", type=float, default=1e-3)
    ap.add_argument("--wd", type=float, default=1e-4)
    ap.add_argument("--patience", type=int, default=10)
    ap.add_argument("--beta", type=float, default=0.05, help="SmoothL1 beta（归一化单位）")
    ap.add_argument("--max-abs-label-um", type=float, default=0.0,
                    help="只保留 |label_um|<=该值的图像（0=不过滤）")
    ap.add_argument("--label-scale", type=float, default=0.0,
                    help="归一化尺度；0=自动取全部数据 max(|label_um|)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--device", default="auto")
    ap.add_argument("--smoke", action="store_true", help="1 epoch, batch 16，跳过 LOO/基线/绘图")
    ap.add_argument("--skip-loo", action="store_true")
    ap.add_argument("--skip-baseline", action="store_true")
    ap.add_argument("--finish", action="store_true",
                    help="只补 MLP 基线 + 汇总 results.json（复用已保存模型与日志，不重训）")
    args = ap.parse_args()

    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    if args.device == "auto":
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    else:
        device = torch.device(args.device)
    os.makedirs(args.out, exist_ok=True)
    log_path = os.path.join(args.out, "train_log.txt")
    log_file = open(log_path, "a", encoding="utf-8")

    def log(msg):
        print(msg)
        log_file.write(str(msg) + "\n")
        log_file.flush()

    cfg = {
        "epochs": 1 if args.smoke else args.epochs,
        "batch": 16 if args.smoke else args.batch,
        "lr_backbone": args.lr_backbone,
        "lr_head": args.lr_head,
        "wd": args.wd,
        "patience": args.patience,
        "seed": args.seed,
        "beta": args.beta,
        "max_abs_label_um": args.max_abs_label_um,
    }

    if args.finish:
        run_finish(args, device, log, cfg)
        log_file.close()
        return

    log(f"=== 单帧对焦回归训练 === device={device} config={cfg}")
    rows = load_rows(args.data)
    log(f"数据行数: {len(rows)}")
    rows, n_drop = filter_rows(rows, args.max_abs_label_um)
    if n_drop:
        log(f"过滤 |Δz| > {args.max_abs_label_um:.0f} μm: "
            f"丢弃 {n_drop} 张 ({n_drop / (n_drop + len(rows)) * 100:.1f}%), 保留 {len(rows)} 张")
        if not rows:
            raise SystemExit("过滤后无数据，请检查 --max-abs-label-um")
        kept_abs = sorted(abs(float(r["label_um"])) for r in rows)
        log(f"过滤后标签范围: |Δz| {kept_abs[0]:.0f} ~ {kept_abs[-1]:.0f} μm")
    label_scale = args.label_scale if args.label_scale > 0 else max(
        abs(float(r["label_um"])) for r in rows
    )
    cfg["label_scale"] = label_scale
    ys = [float(r["label_um"]) / label_scale for r in rows]
    log(f"LABEL_SCALE = {label_scale:.0f} μm（归一化标签范围 {min(ys):.3f}..{max(ys):.3f}）")

    # 预处理张量缓存（所有 epoch / LOO 折共享，只解码一次）
    cache_file = os.path.join(args.out, "preprocessed.pt")
    if load_preproc_cache(cache_file):
        log(f"预处理缓存加载: {len(_PREPROC_CACHE)} 张 <- {cache_file}")
    else:
        t0 = time.perf_counter()
        for r in rows:
            preprocess(r["path"])
        save_preproc_cache(cache_file)
        log(f"预处理缓存构建: {_PREPROC_DECODED} 张解码, {time.perf_counter() - t0:.1f}s -> {cache_file}")
    for sid in sorted({r["sample_id"] for r in rows}):
        sub = [r for r in rows if r["sample_id"] == sid]
        labels = sorted(int(r["label_um"]) for r in sub)
        log(f"  sample {sid}: n={len(sub)}, label {labels[0]}..{labels[-1]} um")
    missing = [r["path"] for r in rows if not os.path.exists(r["path"])]
    if missing:
        raise SystemExit(f"缺失文件 {len(missing)} 个: {missing[:3]}")

    results = {"config": cfg, "device": str(device)}

    # ---------- 主划分 ----------
    train_r, val_r, test_r = split_primary(rows, seed=args.seed)
    log(f"主划分: train={len(train_r)} val={len(val_r)} test={len(test_r)}")
    with open(os.path.join(args.out, "split_primary.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["split", "sample_id", "idx", "label_um", "path"])
        for tag, group in (("train", train_r), ("val", val_r), ("test", test_r)):
            for r in group:
                w.writerow([tag, r["sample_id"], r["idx"], r["label_um"], r["path"]])

    # ---------- ResNet18 主模型 ----------
    log("\n[ResNet18 主模型]")
    model = build_resnet().to(device)
    model, best_mae, hist = train_model(
        model,
        FocusDataset(train_r, augment=True, label_scale=label_scale),
        FocusDataset(val_r, label_scale=label_scale),
        device, cfg, "resnet-primary", log,
    )
    torch.save(model.state_dict(), os.path.join(args.out, "best_resnet.pt"))
    log(f"[resnet-primary] 保存 best_resnet.pt (val_mae={best_mae:.2f}um)")
    try:
        dummy = torch.randn(1, 3, IMG_SIZE, IMG_SIZE, device=device)
        model.eval()
        with torch.no_grad():
            torch.onnx.export(model, dummy, os.path.join(args.out, "best_resnet.onnx"),
                              opset_version=13, input_names=["img"], output_names=["delta_um"])
        log("[resnet-primary] ONNX 导出完成")
    except Exception as e:
        log(f"[警告] ONNX 导出失败: {e}")
    preds, labels = predict(model, test_r, device, label_scale=label_scale)
    prim_metrics = compute_metrics(preds, labels)
    results["primary"] = prim_metrics
    results["primary_history"] = hist[-1] if hist else None
    results["label_scale"] = label_scale
    log(f"[resnet-primary] test: {json.dumps(prim_metrics, ensure_ascii=False)}")
    if not args.smoke:
        save_plots(preds, labels, prim_metrics, args.out, "primary")

    # ---------- 推理耗时 ----------
    if device.type == "cuda":
        ms = measure_inference_ms(model, device)
        results["inference_ms_gpu"] = ms
        log(f"GPU 推理耗时: {ms:.2f} ms/帧")

    # ---------- LOO 泛化探针 ----------
    if not args.smoke and not args.skip_loo:
        log("\n[留一样本交叉验证]")
        loo_metrics = []
        for train_l, val_l, test_l, held in loo_folds(rows, seed=args.seed):
            log(f"  fold: 留出 sample {held}, train={len(train_l)} val={len(val_l)} test={len(test_l)}")
            m = build_resnet().to(device)
            m, _, _ = train_model(
                m, FocusDataset(train_l, augment=True, label_scale=label_scale),
                FocusDataset(val_l, label_scale=label_scale),
                device, cfg, f"loo-{held}", log,
            )
            p, y = predict(m, test_l, device, label_scale=label_scale)
            met = compute_metrics(p, y)
            met["held_sample"] = held
            loo_metrics.append(met)
            log(f"  fold sample{held}: mae={met['mae_um']:.2f} hit10={met['hit10_pct']:.1f}% "
                f"dir_err={met['dir_err_pct']:.1f}%")
        avg = {
            "mae_um": float(np.mean([m["mae_um"] for m in loo_metrics])),
            "hit5_pct": float(np.mean([m["hit5_pct"] for m in loo_metrics])),
            "hit10_pct": float(np.mean([m["hit10_pct"] for m in loo_metrics])),
            "dir_err_pct": float(np.mean([m["dir_err_pct"] for m in loo_metrics])),
        }
        results["loo"] = {"folds": loo_metrics, "mean": avg}
        log(f"LOO 平均: {json.dumps(avg, ensure_ascii=False)}")

    # ---------- MLP 基线 ----------
    if not args.smoke and not args.skip_baseline:
        log("\n[MLP 基线]")
        all_rows = train_r + val_r + test_r
        feats_all = compute_features(all_rows)
        row2idx = {id(r): i for i, r in enumerate(all_rows)}
        Xtr = feats_all[[row2idx[id(r)] for r in train_r]]
        Xva = feats_all[[row2idx[id(r)] for r in val_r]]
        Xte = feats_all[[row2idx[id(r)] for r in test_r]]
        mu, sd = Xtr.mean(0), Xtr.std(0) + 1e-8
        Xtr, Xva, Xte = (Xtr - mu) / sd, (Xva - mu) / sd, (Xte - mu) / sd
        ytr = np.array([float(r["label_um"]) for r in train_r]) / label_scale
        yva = np.array([float(r["label_um"]) for r in val_r]) / label_scale
        yte = np.array([float(r["label_um"]) for r in test_r]) / label_scale
        base = train_mlp(Xtr, ytr, Xva, yva, Xte, yte, device, cfg, log, label_scale)
        results["baseline_mlp"] = base
        log(f"[mlp-baseline] test: {json.dumps(base, ensure_ascii=False)}")

    with open(os.path.join(args.out, "results.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    log(f"\n完成。结果: {os.path.join(args.out, 'results.json')}")
    log_file.close()


if __name__ == "__main__":
    main()
