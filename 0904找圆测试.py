# # -*- coding: utf-8 -*-
# """使用局部背景校正和轮廓分析定位最终成像中的产品圆。"""
# import math
# import os
# from typing import Optional
# import numpy as np
# from backend.inspection_config import CircleDetectionConfig
# from backend.inspection_types import CircleCandidate
#
#
# # 这些是第一版固定的形状约束。它们不进入 GUI，避免一次引入过多参数。
# _MIN_CIRCULARITY = 0.55
# _MIN_ASPECT_RATIO = 0.75
# _MAX_ASPECT_RATIO = 1.30
# _BACKGROUND_SIGMA_RATIO = 1.20
# _OPEN_KERNEL_RADIUS_RATIO = 0.20
#
#
# class ContourCircleDetector:
#     """从不均匀背景中分离深色端面，再按轮廓形状筛选产品圆。"""
#
#     # detect函数返回，增加binary_debug
#     def detect(
#             self,
#             image,
#             config: CircleDetectionConfig,
#     ) -> tuple[
#         list[CircleCandidate],
#         Optional[int],
#         bool,
#         list[str],
#         Optional[np.ndarray],  # 新增：调试用binary二值图，业务调用忽略即可
#     ]:
#         validation_errors = _validate_detection_config(config)
#         if validation_errors:
#             raise ValueError("；".join(validation_errors))
#
#         import cv2
#         import numpy as np
#
#         grayscale = _prepare_grayscale(image, cv2, np)
#         factor = int(config.downsample_factor)
#         if factor > 1:
#             small_width = max(1, int(round(grayscale.shape[1] / factor)))
#             small_height = max(1, int(round(grayscale.shape[0] / factor)))
#             grayscale = cv2.resize(
#                 grayscale,
#                 (small_width, small_height),
#                 interpolation=cv2.INTER_AREA,
#             )
#         """
#         背景差分
#         """
#         # blurred = cv2.GaussianBlur(
#         #     grayscale,
#         #     (config.blur_kernel_size, config.blur_kernel_size),
#         #     0,
#         # )
#         small_min_radius = config.min_radius_px / factor
#         small_max_radius = config.max_radius_px / factor
#         reference_radius = math.sqrt(
#             max(1.0, small_min_radius) * small_max_radius
#         )
#         #
#         # background_sigma = max(
#         #     20.0,
#         #     reference_radius * _BACKGROUND_SIGMA_RATIO,
#         # )
#         # background = cv2.GaussianBlur(
#         #     blurred,
#         #     (0, 0),
#         #     sigmaX=background_sigma,
#         #     sigmaY=background_sigma,
#         # )
#         # dark_response = cv2.subtract(background, blurred)
#         # _threshold, binary = cv2.threshold(
#         #     dark_response,
#         #     0,
#         #     255,
#         #     cv2.THRESH_BINARY + cv2.THRESH_OTSU,
#         # )
#         # 密集度去分辨
#         """
#         密集度去分辨
#         """
#         # ========== 局部方差纹理分割 ==========
#         # 这三个变量必须保留，后面形态学核、轮廓过滤都要用
#         small_min_radius = config.min_radius_px / factor
#         small_max_radius = config.max_radius_px / factor
#         reference_radius = math.sqrt(
#             max(1.0, small_min_radius) * small_max_radius
#         )
#
#         # 转float32，避免uint8相乘溢出导致方差计算错误
#         gray_float = grayscale.astype(np.float32)
#         win_size = _fit_odd_kernel_size(15, min(grayscale.shape[:2]))
#
#         # boxFilter 计算局部均值、局部平方均值，推导局部方差
#         blur_mean = cv2.boxFilter(gray_float, cv2.CV_32F, (win_size, win_size))
#         blur_sqmean = cv2.boxFilter(gray_float * gray_float, cv2.CV_32F, (win_size, win_size))
#         variance = blur_sqmean - blur_mean * blur_mean
#
#         # 归一化并反转：方差越小（表面越平滑）越亮
#         variance_norm = cv2.normalize(variance, None, 0, 255, cv2.NORM_MINMAX, cv2.CV_8U)
#         _, binary = cv2.threshold(
#             variance_norm, 0, 255,
#             cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
#         )
#
#         # 形态学：先开运算去小噪点，再闭运算填补圆内孔洞
#         open_kernel_size = _fit_odd_kernel_size(
#             reference_radius * _OPEN_KERNEL_RADIUS_RATIO,
#             min(binary.shape[:2]),
#         )
#         kernel = cv2.getStructuringElement(
#             cv2.MORPH_ELLIPSE,
#             (open_kernel_size, open_kernel_size),
#         )
#         binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
#         binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)
#
#         open_kernel_size = _fit_odd_kernel_size(
#             reference_radius * _OPEN_KERNEL_RADIUS_RATIO,
#             min(binary.shape[:2]),
#         )
#         kernel = cv2.getStructuringElement(
#             cv2.MORPH_ELLIPSE,
#             (open_kernel_size, open_kernel_size),
#         )
#         binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
#
#         contours, _hierarchy = cv2.findContours(
#             binary,
#             cv2.RETR_EXTERNAL,
#             cv2.CHAIN_APPROX_SIMPLE,
#         )
#         candidates = []
#         for contour in contours:
#             candidate = _candidate_from_contour(
#                 contour,
#                 image_width=binary.shape[1],
#                 image_height=binary.shape[0],
#                 small_min_radius=small_min_radius,
#                 small_max_radius=small_max_radius,
#                 factor=factor,
#                 cv2=cv2,
#             )
#             if candidate is not None:
#                 candidates.append(candidate)
#
#         warnings: list[str] = []
#         if not candidates:
#             # 返回多一个binary
#             return [], None, False, ["轮廓找圆未找到候选圆"], binary
#
#         candidates.sort(key=lambda item: item.score, reverse=True)
#         raw_candidate_count = len(candidates)
#         candidates = _deduplicate_candidates(
#             candidates,
#             min_center_distance_px=config.min_center_distance_px,
#         )
#         detected_count = len(candidates)
#         if detected_count == 0:
#             return [], None, False, ["轮廓找圆未产生有效候选圆"], binary
#
#         if raw_candidate_count != detected_count:
#             warnings.append(
#                 f"轮廓原始候选 {raw_candidate_count} 个，"
#                 f"按圆心距离去重后 {detected_count} 个"
#             )
#
#         if detected_count != config.expected_circle_count:
#             warnings.append(
#                 f"预期检测到 {config.expected_circle_count} 个圆，"
#                 f"轮廓去重后检测到 {detected_count} 个"
#             )
#
#         selected_candidates = candidates[:config.expected_circle_count]
#         highest_score_candidate = selected_candidates[0]
#         selected_candidates.sort(
#             key=lambda item: (item.center_x, item.center_y, item.radius_px)
#         )
#         selected_index = next(
#             index
#             for index, candidate in enumerate(selected_candidates)
#             if candidate is highest_score_candidate
#         )
#
#         complete_count = len(selected_candidates) == config.expected_circle_count
#         low_score_candidates = [
#             candidate
#             for candidate in selected_candidates
#             if candidate.score < config.min_candidate_score
#         ]
#         confirmed = complete_count and not low_score_candidates
#         for candidate in low_score_candidates:
#             warnings.append(
#                 f"候选圆中心({candidate.center_x:.1f}, {candidate.center_y:.1f})"
#                 f"圆度评分 {candidate.score:.3f} 低于自动确认阈值"
#                 f" {config.min_candidate_score:.3f}"
#             )
#         # 【重点】返回增加binary调试图
#         return selected_candidates, selected_index, confirmed, warnings, binary
#
#
# class HoughCircleDetector(ContourCircleDetector):
#     """旧类名兼容入口；内部已经不再调用 HoughCircles。"""
#
#
# def _candidate_from_contour(
#     contour,
#     *,
#     image_width: int,
#     image_height: int,
#     small_min_radius: float,
#     small_max_radius: float,
#     factor: int,
#     cv2,
# ) -> Optional[CircleCandidate]:
#     """将一个轮廓按面积、圆度、长宽比和边界条件转换为圆候选。"""
#
#     area = float(cv2.contourArea(contour))
#     perimeter = float(cv2.arcLength(contour, True))
#     if area <= 0 or perimeter <= 0:
#         return None
#
#     x, y, width, height = cv2.boundingRect(contour)
#     if (
#         x <= 0
#         or y <= 0
#         or x + width >= image_width
#         or y + height >= image_height
#     ):
#         # 贴边轮廓通常是画面边缘、阴影或未完整进入视野的端面。
#         return None
#
#     aspect_ratio = width / height
#     if not _MIN_ASPECT_RATIO <= aspect_ratio <= _MAX_ASPECT_RATIO:
#         return None
#
#     equivalent_radius = math.sqrt(area / math.pi)
#     if not small_min_radius <= equivalent_radius <= small_max_radius:
#         return None
#
#     circularity = 4.0 * math.pi * area / (perimeter * perimeter)
#     if not math.isfinite(circularity) or circularity < _MIN_CIRCULARITY:
#         return None
#
#     moments = cv2.moments(contour)
#     if moments["m00"] == 0:
#         return None
#     center_x = moments["m10"] / moments["m00"]
#     center_y = moments["m01"] / moments["m00"]
#
#     return CircleCandidate(
#         center_x=float(center_x) * factor,
#         center_y=float(center_y) * factor,
#         radius_px=float(equivalent_radius) * factor,
#         score=min(1.0, float(circularity)),
#         source="contour",
#     )
#
#
# def _fit_odd_kernel_size(requested_size: float, image_limit: int) -> int:
#     """生成不超过图像短边的正奇数形态学核尺寸。"""
#
#     kernel_size = max(3, int(round(requested_size)))
#     if kernel_size % 2 == 0:
#         kernel_size += 1
#
#     maximum = max(1, int(image_limit))
#     if maximum % 2 == 0:
#         maximum -= 1
#     return max(1, min(kernel_size, maximum))
#
#
# def _deduplicate_candidates(
#     candidates: list[CircleCandidate],
#     *,
#     min_center_distance_px: float,
# ) -> list[CircleCandidate]:
#     """保留高分候选；圆心距离小于配置阈值的候选视为同一端面。"""
#
#     kept = []
#     for candidate in candidates:
#         duplicate = any(
#             math.hypot(
#                 candidate.center_x - existing.center_x,
#                 candidate.center_y - existing.center_y,
#             ) < min_center_distance_px
#             for existing in kept
#         )
#         if not duplicate:
#             kept.append(candidate)
#     return kept
#
#
# def _validate_detection_config(config: CircleDetectionConfig) -> list[str]:
#     """只校验轮廓找圆运行时直接依赖的参数。"""
#
#     errors = []
#     if config.downsample_factor < 1:
#         errors.append("找圆降采样倍数必须至少为 1")
#     if config.blur_kernel_size < 1 or config.blur_kernel_size % 2 == 0:
#         errors.append("找圆模糊核尺寸必须是正奇数")
#     if (
#         not math.isfinite(config.min_center_distance_px)
#         or config.min_center_distance_px <= 0
#     ):
#         errors.append("候选圆心最小距离必须大于 0")
#     if config.min_radius_px < 0:
#         errors.append("候选圆最小半径不能小于 0")
#     if config.max_radius_px <= config.min_radius_px:
#         errors.append("候选圆最大半径必须大于最小半径")
#     if config.expected_circle_count < 1:
#         errors.append("预期圆数量必须至少为 1")
#     if not math.isfinite(config.min_candidate_score) or not (
#         0 <= config.min_candidate_score <= 1
#     ):
#         errors.append("候选圆最低评分必须在 0～1 之间")
#     return errors
#
#
# def _prepare_grayscale(image, cv2, np):
#     """检查输入图像并转换成轮廓分析需要的 uint8 灰度图。"""
#
#     if image is None:
#         raise ValueError("轮廓找圆收到空图像")
#
#     array = np.asarray(image)
#     if array.size == 0:
#         raise ValueError("轮廓找圆收到空图像")
#
#     if array.ndim == 2:
#         grayscale = array
#     elif array.ndim == 3 and array.shape[2] == 1:
#         grayscale = array[:, :, 0]
#     elif array.ndim == 3 and array.shape[2] == 3:
#         grayscale = cv2.cvtColor(array, cv2.COLOR_BGR2GRAY)
#     elif array.ndim == 3 and array.shape[2] == 4:
#         grayscale = cv2.cvtColor(array, cv2.COLOR_BGRA2GRAY)
#     else:
#         raise ValueError("轮廓找圆只支持灰度图、BGR 图或 BGRA 图")
#
#     if grayscale.dtype != np.uint8:
#         finite = np.isfinite(grayscale)
#         if not finite.any():
#             raise ValueError("轮廓找圆图像不包含有限像素")
#         safe = np.where(finite, grayscale, 0)
#         grayscale = cv2.normalize(
#             safe,
#             None,
#             0,
#             255,
#             cv2.NORM_MINMAX,
#         ).astype(np.uint8)
#
#     return np.ascontiguousarray(grayscale)
#
#
# # ====================== 调试部分：全部写死在代码内，无需命令行 ======================
# def _build_test_config(
#     downsample_factor=1,
#     blur_kernel_size=11,
#     min_radius_px=50,
#     max_radius_px=600,
#     min_center_distance_px=100,
#     expected_circle_count=1,
#     min_candidate_score=0.55
# ) -> CircleDetectionConfig:
#     return CircleDetectionConfig(
#         downsample_factor=downsample_factor,
#         blur_kernel_size=blur_kernel_size,
#         min_radius_px=min_radius_px,
#         max_radius_px=max_radius_px,
#         min_center_distance_px=min_center_distance_px,
#         expected_circle_count=expected_circle_count,
#         min_candidate_score=min_candidate_score
#     )
#
#
# def draw_circles_on_image(bgr_img, candidates: list[CircleCandidate]):
#     import cv2
#     out = bgr_img.copy()
#     for idx, cand in enumerate(candidates):
#         cx = int(round(cand.center_x))
#         cy = int(round(cand.center_y))
#         r = int(round(cand.radius_px))
#         cv2.circle(out, (cx, cy), r, (0, 255, 0), 2)
#         cv2.circle(out, (cx, cy), 4, (0, 0, 255), -1)
#         text = f"#{idx} sc:{cand.score:.2f}"
#         cv2.putText(
#             out, text, (cx + 10, cy - 10),
#             cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 1
#         )
#     return out
#
#
# def run_debug():
#     import cv2
#     import numpy as np
#
#     # ========================= 在这里修改调试参数 =========================
#     IMAGE_PATH = r"D:\\GVIMAGES\\saveimg\\20260904_152023_812422.jpg"
#     test_cfg = _build_test_config(
#         downsample_factor=2,  # ✅必开，宽高减半，速度大幅提升，该图目标很大不损失精度
#         blur_kernel_size=11,
#         min_radius_px=180,  # ⚠️关键！大于右上角小圆半径，直接过滤所有小东西
#         max_radius_px=300,  # 略大于实际大圆半径260
#         min_center_distance_px=220,  # 两个圆心最小间隔，防止重复检测同一个圆
#         expected_circle_count=5,  # 期望检测5个圆
#         min_candidate_score=0.70  # 最低圆度门槛，过滤不规则颗粒
#     )
#     OUT_RESULT_PATH = "D:\\GVIMAGES\saveimg\\test\\detect_result.jpg"
#     OUT_BINARY_PATH = "D:\\GVIMAGES\saveimg\\test\\binary_debug.jpg"
#     SHOW_WINDOW = True   # 开关：是否弹出OpenCV窗口显示图片
#     # =====================================================================
#
#     if not os.path.exists(IMAGE_PATH):
#         print(f"错误：文件不存在 {IMAGE_PATH}")
#         return
#
#     raw_bgr = cv2.imread(IMAGE_PATH)
#     if raw_bgr is None:
#         print(f"读取图像失败: {IMAGE_PATH}")
#         return
#
#     detector = ContourCircleDetector()
#     # 接收多返回值 binary_debug_img
#     candidate_list, sel_idx, confirm_ok, warns, binary_debug_img = detector.detect(raw_bgr, test_cfg)
#
#     print("="*60)
#     print(f"检测完成，自动确认状态：{confirm_ok}")
#     print(f"选中最高分索引：{sel_idx}")
#     print(f"警告列表：")
#     for w in warns:
#         print(f"  - {w}")
#     print(f"\n候选圆数量：{len(candidate_list)}")
#     for i, c in enumerate(candidate_list):
#         print(f"  [{i}] center=({c.center_x:.2f}, {c.center_y:.2f}) radius={c.radius_px:.2f} score={c.score:.3f} src={c.source}")
#
#     draw_result = draw_circles_on_image(raw_bgr, candidate_list)
#     cv2.imwrite(OUT_RESULT_PATH, draw_result)
#     cv2.imwrite(OUT_BINARY_PATH, binary_debug_img)
#     print(f"\n输出结果图已保存：{OUT_RESULT_PATH} , {OUT_BINARY_PATH}")
#
#     # ============ 图片显示功能在这里！ ============
#     if SHOW_WINDOW:
#         cv2.namedWindow("detect_result", cv2.WINDOW_NORMAL)
#         cv2.namedWindow("binary_debug(二值中间图)", cv2.WINDOW_NORMAL)
#         cv2.imshow("detect_result", draw_result)
#         cv2.imshow("binary_debug(二值中间图)", binary_debug_img)
#         print("\n【提示】窗口已打开，按任意键盘按键关闭所有图像窗口")
#         cv2.waitKey(0)       # 0=无限等待按键，按任意键继续
#         cv2.destroyAllWindows()
#
#
#
#
# if __name__ == "__main__":
#     # 直接运行脚本就执行调试，不用传任何命令行参数
#     run_debug()




import cv2
import numpy as np
import math


# ==================== 参数 ====================
IMAGE_PATH = r"D:\\GVIMAGES\\saveimg\\20260904_152023_812422.jpg"

DISPLAY_MAX_W = 1000
DISPLAY_MAX_H = 500

# 高斯滤波：适当压制背景大量小颗粒纹理
GAUSSIAN_KERNEL = (7, 7)
GAUSSIAN_SIGMA = 1.5

# Canny
CANNY_LOW = 20
CANNY_HIGH = 70

# 目标大圆半径
MIN_RADIUS = 200
MAX_RADIUS = 300

# 目标圆理论面积约 3~5 万 px²
MIN_AREA = 20000
MAX_AREA = 60000

# 圆度
MIN_CIRCULARITY = 0.45

TARGET_COUNT = 5


# ==================== 显示 ====================
def show(name, img):
    h, w = img.shape[:2]
    scale = min(DISPLAY_MAX_W / w, DISPLAY_MAX_H / h, 1.0)
    display = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA) if scale < 1.0 else img
    cv2.namedWindow(name, cv2.WINDOW_NORMAL)
    cv2.imshow(name, display)


# ==================== 最小二乘圆拟合 ====================
def fit_circle_least_squares(points):
    points = np.asarray(points, dtype=np.float64)
    x, y = points[:, 0], points[:, 1]
    A = np.column_stack((2 * x, 2 * y, np.ones_like(x)))
    B = x ** 2 + y ** 2
    result, _, _, _ = np.linalg.lstsq(A, B, rcond=None)
    cx, cy, c = result
    radius = math.sqrt(max(0, cx ** 2 + cy ** 2 + c))
    return cx, cy, radius


def main():
    image = cv2.imread(IMAGE_PATH)
    if image is None:
        raise RuntimeError(f"图像读取失败：{IMAGE_PATH}")

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    print(f"Image size: {w} x {h}")

    # 01 原图
    show("01 Original", image)

    # 02 轻微高斯滤波
    blur = cv2.GaussianBlur(gray, GAUSSIAN_KERNEL, GAUSSIAN_SIGMA)
    show("02 Gaussian Blur", blur)

    # 03 Canny边缘
    edges = cv2.Canny(blur, CANNY_LOW, CANNY_HIGH)
    show("03 Canny Edge", edges)

    # 04 全图轮廓
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    print(f"Original contours: {len(contours)}")

    all_contours_img = image.copy()
    cv2.drawContours(all_contours_img, contours, -1, (0, 255, 255), 1)
    show("04 All Contours", all_contours_img)

    # 05 轮廓筛选
    candidates = []
    candidate_img = image.copy()

    for contour in contours:
        if len(contour) < 20:
            continue

        area = cv2.contourArea(contour)
        if area < MIN_AREA or area > MAX_AREA:
            continue

        perimeter = cv2.arcLength(contour, True)
        if perimeter <= 0:
            continue

        circularity = 4.0 * math.pi * area / (perimeter * perimeter)
        if circularity < MIN_CIRCULARITY:
            continue

        (_, _), temp_r = cv2.minEnclosingCircle(contour)
        if not MIN_RADIUS <= temp_r <= MAX_RADIUS:
            continue

        points = contour.reshape(-1, 2)
        cx, cy, radius = fit_circle_least_squares(points)

        if not MIN_RADIUS <= radius <= MAX_RADIUS:
            continue

        candidates.append({
            "contour": contour,
            "cx": cx,
            "cy": cy,
            "radius": radius,
            "area": area,
            "circularity": circularity,
            "point_num": len(points)
        })

        cv2.drawContours(candidate_img, [contour], -1, (0, 255, 0), 2)
        cv2.circle(candidate_img, (round(cx), round(cy)), 4, (0, 0, 255), -1)

    show("05 Filtered Contours", candidate_img)

    # 如果超过5个，优先保留圆度最高的5个
    if len(candidates) > TARGET_COUNT:
        candidates = sorted(candidates, key=lambda c: c["circularity"], reverse=True)[:TARGET_COUNT]

    # 按X坐标从左到右排序
    candidates.sort(key=lambda c: c["cx"])

    # 06 最终拟合结果
    result_img = image.copy()

    for i, c in enumerate(candidates):
        cx, cy, radius = c["cx"], c["cy"], c["radius"]
        center = (round(cx), round(cy))
        r = round(radius)

        cv2.circle(result_img, center, r, (0, 255, 0), 3, cv2.LINE_AA)
        cv2.drawMarker(result_img, center, (0, 0, 255), cv2.MARKER_CROSS, 20, 2)
        cv2.putText(result_img, f"#{i + 1} R={radius:.1f}", (center[0] - 50, center[1] - r - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2, cv2.LINE_AA)

    show("06 Circle Fitting Result", result_img)

    # 输出结果
    print("\n================ Detection Result ================")
    print(f"Circle count: {len(candidates)}")

    for i, c in enumerate(candidates):
        print(f"Circle {i + 1}: Center=({c['cx']:.2f}, {c['cy']:.2f}), Radius={c['radius']:.2f}, "
              f"Diameter={c['radius'] * 2:.2f}, Circularity={c['circularity']:.3f}, Points={c['point_num']}")

    cv2.waitKey(0)
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()