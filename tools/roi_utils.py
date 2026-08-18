import cv2
import numpy as np
from pathlib import Path


def select_roi_interactive(image_path: str):
    """
    打开图片，鼠标框选 ROI，按 Enter 确认，按 ESC 取消。
    返回 (x, y, w, h)。
    """
    img = cv2.imread(image_path)
    if img is None:
        raise FileNotFoundError(f"Cannot read: {image_path}")

    roi = cv2.selectROI("Select ROI - ENTER=confirm, ESC=cancel", img,
                        showCrosshair=True, fromCenter=False)
    cv2.destroyAllWindows()

    x, y, w, h = roi
    if w == 0 or h == 0:
        return None
    return (int(x), int(y), int(w), int(h))


def preview_roi(image_path: str, roi: tuple, save_preview: str = None):
    """在图上画出 ROI 框，预览效果。可选保存预览图。"""
    img = cv2.imread(image_path)
    x, y, w, h = roi
    preview = img.copy()
    cv2.rectangle(preview, (x, y), (x + w, y + h), (0, 255, 0), 2)
    cv2.putText(preview, f"ROI: {w}x{h}", (x, y - 10),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

    if save_preview:
        cv2.imwrite(save_preview, preview)
        print(f"Preview saved: {save_preview}")

    # Show
    cv2.imshow("ROI Preview", preview)
    print("Press any key to close preview...")
    cv2.waitKey(0)
    cv2.destroyAllWindows()


def dynamic_roi_by_template(ref_image_path: str, roi: tuple):
    """
    从参考图的 ROI 区域提取模板，返回一个定位函数。
    该函数对任意新图，用模板匹配找到对应 ROI。
    """
    ref = cv2.imread(ref_image_path)
    x, y, w, h = roi
    template = ref[y:y+h, x:x+w]

    def locate(image):
        """在新图上定位 ROI。支持彩色或灰度。"""
        # 都转灰度做匹配
        if template.ndim == 3:
            tpl_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
        else:
            tpl_gray = template
        if image.ndim == 3:
            img_gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            img_gray = image

        result = cv2.matchTemplate(img_gray, tpl_gray, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)

        th, tw = tpl_gray.shape[:2]
        return (max_loc[0], max_loc[1], tw, th), max_val

    # 保存模板的宽和高
    locate.template_shape = template.shape[:2]
    locate.template = template
    return locate


# ============================================================
# 更新后的 Evaluator，支持动态 ROI
# ============================================================

class DynamicROIEvaluator:
    """
    支持三种 ROI 模式:
      - roi = (x,y,w,h):  固定 ROI，所有图共用
      - roi = "template":  模板匹配，从第一张图提取模板，后续自动定位
      - roi = None:        全图
    """

    def __init__(self, metric: str = "laplacian", roi=None,
                 ref_image_path: str = None):
        self.metric = metric
        self.roi_mode = roi
        self.locate_fn = None

        if roi == "template" and ref_image_path:
            ref = cv2.imread(ref_image_path)
            h, w = ref.shape[:2]
            center_roi = (w//4, h//4, w//2, h//2)  # 默认取中央 50%
            # 也可以先 select_roi_interactive 选，这里简化
            self.locate_fn = dynamic_roi_by_template(ref_image_path, center_roi)

    def evaluate(self, image_path: str, roi=None) -> float:
        from focus_search import METRICS  # reuse metrics
        img = cv2.imread(image_path)
        if img is None:
            raise FileNotFoundError(f"Cannot read: {image_path}")

        # 确定本次使用的 ROI
        if roi is not None:
            active_roi = roi
        elif self.locate_fn is not None:
            active_roi, confidence = self.locate_fn(img)
            # confidence 低于阈值时可以报警
        elif isinstance(self.roi_mode, tuple) and len(self.roi_mode) == 4:
            active_roi = self.roi_mode
        else:
            active_roi = None

        return METRICS[self.metric](img, active_roi)


# ============================================================
# ROI 推荐尺寸检查工具
# ============================================================

def check_roi_quality(image_path: str, roi: tuple) -> dict:
    """
    检查一个 ROI 是否适合做清晰度评价:
      - 纹理度 (Laplacian std > 阈值)
      - 对比度 (灰度 std > 阈值)
      - 边缘密度
    """
    img = cv2.imread(image_path)
    x, y, w, h = roi
    patch = img[y:y+h, x:x+w]
    if patch.ndim == 3:
        gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
    else:
        gray = patch

    lap = cv2.Laplacian(gray, cv2.CV_64F)
    edges = cv2.Canny(gray, 50, 150)
    edge_density = np.sum(edges > 0) / edges.size

    return {
        "roi_size":       f"{w}x{h} ({w*h/1000:.1f}k pixels)",
        "contrast_std":   float(np.std(gray)),
        "laplacian_std":  float(np.std(lap)),
        "edge_density":   float(edge_density),
        "verdict":        "OK" if edge_density > 0.02 and np.std(gray) > 15
                               else "WARN: low texture, may be unreliable",
    }


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python roi_utils.py select <image_path>        # Interactive ROI")
        print("  python roi_utils.py check  <image_path> <x> <y> <w> <h>")
        print("  python roi_utils.py preview <image_path> <x> <y> <w> <h>")
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "select":
        path = sys.argv[2]
        roi = select_roi_interactive(path)
        if roi:
            print(f"ROI: {roi}")  # (x, y, w, h)
            check_roi_quality(path, roi)
        else:
            print("ROI selection cancelled.")

    elif cmd == "check":
        path = sys.argv[2]
        roi = (int(sys.argv[3]), int(sys.argv[4]),
               int(sys.argv[5]), int(sys.argv[6]))
        result = check_roi_quality(path, roi)
        for k, v in result.items():
            print(f"  {k}: {v}")

    elif cmd == "preview":
        path = sys.argv[2]
        roi = (int(sys.argv[3]), int(sys.argv[4]),
               int(sys.argv[5]), int(sys.argv[6]))
        preview_roi(path, roi)
