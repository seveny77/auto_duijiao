# -*- coding: utf-8 -*-
"""验证新模型在 D:\\scan\\mpo\\17 上的效果（需先处理 17 成带标签数据）"""
import csv, glob, os, re
import numpy as np
import cv2
from train_dlfocus import DLDistanceModel

MODEL_PATH = r"F:\项目\自动对焦\code\ct-roi\dlfocus_out\best_resnet.pt"
FOLDER = r"D:\scan\SC\25"
OUT_CSV = r"F:\项目\自动对焦\code\ct-roi\dlfocus_out\mpo17_model_test.csv"

model = DLDistanceModel(MODEL_PATH)

rows = []
for p in sorted(glob.glob(os.path.join(FOLDER, "*.jpg"))):
    m = re.match(r"idx(\d+)_delta([+-]?\d+)um\.jpg", os.path.basename(p))
    if not m:
        print("跳过（文件名不含标签）:", os.path.basename(p))
        continue
    idx, label = int(m.group(1)), int(m.group(2))
    img = cv2.imread(p)
    pred = model.predict_frame(img)
    rows.append({"idx": idx, "label_um": label, "pred_um": pred,
                 "err_um": pred - label,
                 "sign_ok": label == 0 or np.sign(pred) == np.sign(label)})

labels = np.array([r["label_um"] for r in rows], float)
preds = np.array([r["pred_um"] for r in rows], float)
err = preds - labels
mask = np.abs(labels) >= 1
pos, neg = labels > 0, labels < 0

print(f"样本数: {len(rows)}")
print(f"标签范围: {labels.min():.0f} ~ {labels.max():.0f} μm（正 {pos.sum()} / 负 {neg.sum()}）")
print(f"MAE: {np.abs(err).mean():.1f} μm | RMSE: {np.sqrt((err**2).mean()):.1f} μm")
print(f"方向错误率: 总体 {np.mean(np.sign(preds[mask]) != np.sign(labels[mask]))*100:.1f}% | "
      f"正侧 {np.mean(np.sign(preds[pos]) != np.sign(labels[pos]))*100:.1f}% | "
      f"负侧 {np.mean(np.sign(preds[neg]) != np.sign(labels[neg]))*100:.1f}%")
print(f"hit5: {np.mean(np.abs(err)<=5)*100:.1f}% | hit10: {np.mean(np.abs(err)<=10)*100:.1f}% | "
      f"hit15: {np.mean(np.abs(err)<=15)*100:.1f}% | hit30: {np.mean(np.abs(err)<=30)*100:.1f}% | hit50: {np.mean(np.abs(err)<=50)*100:.1f}%")
print(f"预测范围: {preds.min():.1f} ~ {preds.max():.1f} μm")

print("\n按 |Δz| 分区间:")
edges = [0, 50, 100, 200, 300, 500, 600, 1e12]
abs_l = np.abs(labels)
for i in range(len(edges) - 1):
    m_bin = (abs_l >= edges[i]) & (abs_l < edges[i + 1])
    if not m_bin.any():
        continue
    e = err[m_bin]
    dm = m_bin & mask
    dir_err = np.mean(np.sign(preds[dm]) != np.sign(labels[dm])) * 100 if dm.any() else float("nan")
    print(f"  {edges[i]:>4}~{edges[i + 1]:<6} μm: n={m_bin.sum():>3}, MAE={np.abs(e).mean():6.1f}, "
          f"hit5={np.mean(np.abs(e) <= 5) * 100:5.1f}%, hit10={np.mean(np.abs(e) <= 10) * 100:5.1f}%, "
          f"hit15={np.mean(np.abs(e) <= 15) * 100:5.1f}%, hit30={np.mean(np.abs(e) <= 30) * 100:5.1f}%, 方向错={dir_err:4.1f}%")

with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=["idx", "label_um", "pred_um", "err_um", "sign_ok"])
    w.writeheader()
    w.writerows(rows)
print(f"\n已保存: {OUT_CSV}")