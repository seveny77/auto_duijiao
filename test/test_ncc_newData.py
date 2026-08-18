# test_ncc_newdata.py
"""用已有模板在新数据上跑 NCC 搜索"""

import glob
import time
import numpy as np
import cv2

from focus_template import FocusTemplate
from search import CoarseToFineSearch, NCCSearch           # 推荐
from adapters.evaluator_opencv import OpenCVSharpnessEvaluator

# ── 配置 ──────────────────────────────
TEMPLATE_PATH = "../data/template.json"  # 已有的 0729 模板
IMAGE_DIR = r"G:\\0724\\新建文件夹(2)\\3-3mm"
ROI = (2077, 2219, 300, 300)

# ── 加载模板 ──────────────────────────
template = FocusTemplate.load(TEMPLATE_PATH)
print(f"模板: peak={template.peak_position}, FWHM={template.peak_width:.2f}")

# ── 加载新图片 ────────────────────────
images_paths = sorted(glob.glob(IMAGE_DIR + "/*.bmp") or glob.glob(IMAGE_DIR + "/*.png"))
n = len(images_paths)
print(f"新数据: {n} 张图片")

images_data = []
for p in images_paths:
    arr = np.fromfile(p, dtype=np.uint8)
    images_data.append(cv2.imdecode(arr, cv2.IMREAD_COLOR))

# ── 全扫（真值） ──────────────────────
evaluator = OpenCVSharpnessEvaluator()
print("全扫中...")
t0 = time.perf_counter()
all_scores = []
for img in images_data:
    all_scores.append(evaluator.evaluate_image(img, ROI))
time_full = time.perf_counter() - t0
true_peak = max(range(n), key=lambda i: all_scores[i])
print(f"全扫: {n} 张, {time_full:.3f}s, 真峰 index={true_peak}, score={all_scores[true_peak]:.1f}")

# ── NCC 搜索（用已有模板） ────────────
print("NCC 搜索中...")
t0 = time.perf_counter()
search = NCCSearch(n, template=template)
visited = []

idx = search.first_index
score = evaluator.evaluate_image(images_data[idx], ROI)
visited.append(idx)

while True:
    idx, done, best_i, best_s = search.next(score)
    if done:
        break
    score = evaluator.evaluate_image(images_data[idx], ROI)
    visited.append(idx)

time_ncc = time.perf_counter() - t0

# ── 报告 ──────────────────────────────
s = search.stats
print(f"\n{'='*55}")
print(f"NCC 搜索: {s['eval_count']} 张 (-{s['reduction_pct']:.0f}%), {time_ncc:.3f}s")
print(f"NCC={s['ncc_max']:.4f}, 估计偏移 Δz={s['ncc_shift']}")
print(f"预测峰: {s['predicted_peak']}, 真峰: {true_peak}")
print(f"误差:   {abs(s['predicted_peak'] - true_peak)} idx")
print(f"quality: {s['quality']}")
print(f"访问序列: {visited}")
print(f"最终 best: idx={best_i}, score={best_s:.1f}")
