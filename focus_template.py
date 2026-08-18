# focus_template.py
# -*- coding: utf-8 -*-
"""FocusTemplate: 离线标定的焦点响应曲线模板"""

import json


class FocusTemplate:
    """存储全扫归一化曲线及派生特征，支持 JSON 序列化。"""

    def __init__(self, curve, peak_position, peak_width, shape_descriptor, z_offset, meta):
        self.curve = curve
        self.peak_position = peak_position
        self.peak_width = peak_width
        self.shape_descriptor = shape_descriptor
        self.z_offset = z_offset
        self.meta = meta

    # ── 工厂方法 ──────────────────────────────────────────────
    @staticmethod
    def from_fullscan(scores, roi=None, total_images=None):
        n = len(scores)
        min_score = min(scores)
        max_score = max(scores)

        # 1. 峰位置
        peak_position = max(range(n), key=lambda i: scores[i])

        # 2. min-max 归一化
        denom = max_score - min_score
        if denom == 0:
            curve = [0.0] * n
        else:
            curve = [(s - min_score) / denom for s in scores]

        # 3. FWHM（线性插值半高交点）
        half = (max_score + min_score) / 2.0

        # 向左找半高交点
        left_idx = peak_position
        while left_idx > 0 and scores[left_idx - 1] > half:
            left_idx -= 1
        if left_idx > 0 and scores[left_idx] >= half:
            # 线性插值
            frac = (half - scores[left_idx - 1]) / (scores[left_idx] - scores[left_idx - 1])
            left_cross = left_idx - 1 + frac
        else:
            left_cross = float(left_idx)

        # 向右找半高交点
        right_idx = peak_position
        while right_idx < n - 1 and scores[right_idx + 1] > half:
            right_idx += 1
        if right_idx < n - 1 and scores[right_idx] >= half:
            frac = (scores[right_idx] - half) / (scores[right_idx] - scores[right_idx + 1])
            right_cross = right_idx + frac
        else:
            right_cross = float(right_idx)

        peak_width = right_cross - left_cross

        # 4. shape_descriptor（±2×FWHM）
        half_range = int(2.0 * peak_width)
        lo = max(0, peak_position - half_range)
        hi = min(n - 1, peak_position + half_range)
        shape_descriptor = curve[lo:hi + 1]
        z_offset = list(range(lo - peak_position, hi - peak_position + 1))

        # 5. meta
        meta = {
            "total_images": total_images if total_images is not None else n,
            "roi": list(roi) if roi else None,
            "score_min": min_score,
            "score_max": max_score,
        }

        return FocusTemplate(
            curve=curve,
            peak_position=peak_position,
            peak_width=peak_width,
            shape_descriptor=shape_descriptor,
            z_offset=z_offset,
            meta=meta,
        )

    # ── 序列化 ────────────────────────────────────────────────
    def to_dict(self):
        return {
            "curve": self.curve,
            "peak_position": self.peak_position,
            "peak_width": self.peak_width,
            "shape_descriptor": self.shape_descriptor,
            "z_offset": self.z_offset,
            "meta": self.meta,
        }

    def save(self, path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @staticmethod
    def load(path):
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        return FocusTemplate(
            curve=d["curve"],
            peak_position=d["peak_position"],
            peak_width=d["peak_width"],
            shape_descriptor=d["shape_descriptor"],
            z_offset=d["z_offset"],
            meta=d["meta"],
        )
