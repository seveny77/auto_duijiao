# -*- coding: utf-8 -*-
"""查看 FocusTemplate JSON 的曲线：归一化分数 vs µm 位置，标出峰位与 FWHM。

用法:
  python plot_template.py --template data\\template_real.json
  python plot_template.py --template data\\template_real.json --out features_z_out\\template_real.png
"""

import argparse
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from focus_template import FocusTemplate


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", default=r"data\template_real.json")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    t = FocusTemplate.load(args.template)
    start = t.meta.get("start_um", 0)
    step = t.meta.get("step_um", 1)
    n = len(t.curve)

    # 含尾不含首：第 i 帧位置 = start + (i+1)*step
    pos = [start + (i + 1) * step for i in range(n)]
    peak_um = start + (t.peak_position + 1) * step
    half = (t.meta.get("score_min", 0) + t.meta.get("score_max", 0)) / 2.0

    fig, ax = plt.subplots(figsize=(12, 4.5))
    ax.plot(pos, t.curve, lw=1.2, color="#1f77b4", label="normalized curve")
    ax.axvline(peak_um, color="red", ls="--", lw=1, label=f"peak {peak_um:.0f}um")
    # FWHM（按 index 换算 µm）
    half_w_um = t.peak_width * step
    ax.axvspan(peak_um - half_w_um / 2, peak_um + half_w_um / 2,
               color="orange", alpha=0.15, label=f"FWHM {half_w_um:.0f}um")
    ax.set_xlabel("position (um)")
    ax.set_ylabel("normalized score")
    ax.set_title(f"{os.path.basename(args.template)}  peak_idx={t.peak_position} "
                 f"FWHM={t.peak_width:.1f} frames ({half_w_um:.0f}um)  "
                 f"n={n} step={step}um")
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()

    out = args.out or (os.path.splitext(args.template)[0] + "_curve.png")
    fig.savefig(out, dpi=110)
    print(f"曲线已保存: {out}")
    print(f"峰 index={t.peak_position} -> {peak_um:.0f}um")
    print(f"FWHM={t.peak_width:.1f} 帧 = {half_w_um:.0f}um")


if __name__ == "__main__":
    main()
