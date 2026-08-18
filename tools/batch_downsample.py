# batch_downsample.py
"""批量降采样图片 — 直接在下方改配置"""

import glob
import os
import cv2
import numpy as np

# =============================================
# 配置：改下面三行
# =============================================
INPUT_DIR   = r"G:\\0724\\新建文件夹(2)\\2-3mm"           # 输入图片文件夹
OUTPUT_DIR  = r"G:\\0724\\新建文件夹(2)\\2-3mm-1024"           # 输出图片文件夹
WIDTH       = 1024           # 目标宽度
HEIGHT      = 1024           # 目标高度
# =============================================


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    extensions = ["*.bmp", "*.png", "*.jpg", "*.jpeg", "*.tiff"]
    paths = []
    for ext in extensions:
        paths.extend(glob.glob(os.path.join(INPUT_DIR, ext)))
    paths = sorted(paths)

    if not paths:
        print(f"{INPUT_DIR} 下没有图片")
        return

    print(f"找到 {len(paths)} 张图片")
    print(f"降采样到 {WIDTH}x{HEIGHT}")
    print(f"保存到 {OUTPUT_DIR}\n")

    for i, p in enumerate(paths):
        arr = np.fromfile(p, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)

        orig_w, orig_h = img.shape[1], img.shape[0]
        if orig_w != WIDTH or orig_h != HEIGHT:
            img = cv2.resize(img, (WIDTH, HEIGHT), interpolation=cv2.INTER_LINEAR)

        name = os.path.basename(p)
        out_path = os.path.join(OUTPUT_DIR, name)
        ext = os.path.splitext(name)[1]
        success, buf = cv2.imencode(ext, img)
        if success:
            buf.tofile(out_path)

        if (i + 1) % 50 == 0 or i == 0:
            print(f"  [{i+1:4d}/{len(paths)}] {name}  ({orig_w}x{orig_h} -> {WIDTH}x{HEIGHT})")

    print(f"\n完成，{len(paths)} 张已保存到 {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
