# -*- coding: utf-8 -*-
"""局部纹理分割找圆的独立性能测试工具。

该脚本不加载 GUI、YOLO 或 CUDA。图像只读取一次，基准耗时只覆盖内存
图像上的找圆计算，不包含 JPEG 解码、结果图编码和写盘。

示例：
    python -B tools/test_texture_circle_detection.py D:\\GVIMAGES\\sample.jpg
    python -B tools/test_texture_circle_detection.py D:\\GVIMAGES\\sample.jpg \
        --min-radius 220 --max-radius 320 --expected-count 5 --runs 20
"""

import argparse
import math
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Circle:
    center_x: float
    center_y: float
    radius_px: float
    circularity: float
    residual_ratio: float
    score: float
    source: str = "texture"
    edge_coverage: float = 0.0
    texture_contrast: float = 0.0


@dataclass
class DetectionDebug:
    texture_map: np.ndarray
    threshold_mask: np.ndarray
    mask: np.ndarray
    candidates: list[Circle]
    timings_ms: dict[str, float]
    connected_component_count: int
    preselected_component_count: int


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="局部方差纹理分割找圆：输出候选圆、调试图和 P95 耗时。"
    )
    parser.add_argument("image", help="待测试图像路径")
    parser.add_argument("--output-dir", default="", help="输出目录")
    parser.add_argument("--downsample", type=int, default=4, help="降采样倍率")
    parser.add_argument(
        "--variance-window",
        type=int,
        default=15,
        help="局部方差窗口边长（降采样图坐标，正奇数）",
    )
    parser.add_argument(
        "--open-kernel",
        type=int,
        default=7,
        help="形态学开运算核边长（降采样图坐标，正奇数）",
    )
    parser.add_argument(
        "--close-kernel",
        type=int,
        default=15,
        help="形态学闭运算核边长（降采样图坐标，正奇数）",
    )
    parser.add_argument("--min-radius", type=float, default=220.0)
    parser.add_argument("--max-radius", type=float, default=320.0)
    parser.add_argument("--min-center-distance", type=float, default=500.0)
    parser.add_argument("--expected-count", type=int, default=5)
    parser.add_argument("--min-circularity", type=float, default=0.45)
    parser.add_argument(
        "--max-residual-ratio",
        type=float,
        default=0.12,
        help="轮廓点到拟合圆的平均残差 / 半径上限",
    )
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument(
        "--no-hough-fallback",
        action="store_true",
        help="候选圆不足时不启用 Hough 圆心补检",
    )
    parser.add_argument(
        "--hough-param1",
        type=float,
        default=70.0,
        help="Hough 边缘阈值，仅在缺圆补检时使用",
    )
    parser.add_argument(
        "--hough-param2",
        type=float,
        default=50.0,
        help="Hough 累加器阈值，仅在缺圆补检时使用",
    )
    return parser


def read_image(path: Path) -> np.ndarray:
    """支持中文 Windows 路径。"""

    try:
        payload = np.fromfile(str(path), dtype=np.uint8)
    except OSError as error:
        raise ValueError(f"无法读取图像文件: {error}") from error
    image = cv2.imdecode(payload, cv2.IMREAD_UNCHANGED)
    if image is None or image.size == 0:
        raise ValueError("OpenCV 无法解码图像")
    return image


def as_gray(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return image
    if image.ndim == 3 and image.shape[2] == 1:
        return image[:, :, 0]
    if image.ndim == 3 and image.shape[2] == 3:
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    if image.ndim == 3 and image.shape[2] == 4:
        return cv2.cvtColor(image, cv2.COLOR_BGRA2GRAY)
    raise ValueError("仅支持灰度图、BGR 图或 BGRA 图")


def require_odd_positive(name: str, value: int):
    if value < 3 or value % 2 == 0:
        raise ValueError(f"{name} 必须是不小于 3 的正奇数")


def fit_circle_least_squares(points: np.ndarray) -> tuple[float, float, float]:
    """对轮廓点拟合 x²+y²=2cx+2cy+c。"""

    values = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    x = values[:, 0]
    y = values[:, 1]
    matrix = np.column_stack((2.0 * x, 2.0 * y, np.ones_like(x)))
    target = x * x + y * y
    cx, cy, constant = np.linalg.lstsq(matrix, target, rcond=None)[0]
    radius = math.sqrt(max(0.0, cx * cx + cy * cy + constant))
    return float(cx), float(cy), float(radius)


def _is_border_component(x: int, y: int, w: int, h: int, width: int, height: int) -> bool:
    return x <= 0 or y <= 0 or x + w >= width or y + h >= height


def _deduplicate(candidates: list[Circle], min_center_distance_px: float) -> list[Circle]:
    kept: list[Circle] = []
    # 纹理连通域完整时，其轮廓拟合半径通常比 Hough 半径更可靠；Hough
    # 仅补足缺失端面，不能因评分更高覆盖已确认的纹理候选。
    for candidate in sorted(
        candidates,
        key=lambda item: (item.source == "texture", item.score),
        reverse=True,
    ):
        if all(
            math.hypot(
                candidate.center_x - existing.center_x,
                candidate.center_y - existing.center_y,
            ) >= min_center_distance_px
            for existing in kept
        ):
            kept.append(candidate)
    return kept


def _sample_ring_values(
    image: np.ndarray,
    center_x: float,
    center_y: float,
    radius: float,
    angles: np.ndarray,
) -> np.ndarray:
    """最近邻采样一个圆周；超出图像范围时返回空数组。"""

    x = np.rint(center_x + radius * np.cos(angles)).astype(np.int32)
    y = np.rint(center_y + radius * np.sin(angles)).astype(np.int32)
    valid = (
        (x >= 0)
        & (x < image.shape[1])
        & (y >= 0)
        & (y < image.shape[0])
    )
    if valid.mean() < 0.95:
        return np.empty(0, dtype=np.float32)
    return image[y[valid], x[valid]].astype(np.float32)


def _refine_hough_candidate(
    gradient: np.ndarray,
    variance_u8: np.ndarray,
    global_edge_threshold: float,
    center_x: float,
    center_y: float,
    min_radius: float,
    max_radius: float,
) -> Circle | None:
    """用圆周梯度选半径，并用内外纹理差抑制 Hough 假圆。"""

    angles = np.linspace(0.0, 2.0 * math.pi, 180, endpoint=False)
    radius_values = np.arange(
        max(2.0, min_radius),
        max_radius + 0.5,
        1.0,
    )
    best_radius = 0.0
    best_strength = -1.0
    best_coverage = 0.0
    for radius in radius_values:
        values = _sample_ring_values(
            gradient,
            center_x,
            center_y,
            float(radius),
            angles,
        )
        if values.size == 0:
            continue
        # 使用上四分位均值，避免完整圆周中少量弱边缘拉低得分。
        strength = float(np.mean(np.partition(values, int(values.size * 0.75))[int(values.size * 0.75):]))
        coverage = float(np.mean(values >= global_edge_threshold))
        combined = strength * (0.45 + 0.55 * coverage)
        if combined > best_strength:
            best_radius = float(radius)
            best_strength = combined
            best_coverage = coverage

    if best_strength <= 0.0 or best_coverage < 0.18:
        return None

    # 仅在候选周边的小块中计算内圆和外环，避免每个 Hough 候选都扫描
    # 整张 1280×640 图像。
    padding = int(math.ceil(best_radius * 1.50))
    x0 = max(0, int(math.floor(center_x)) - padding)
    x1 = min(variance_u8.shape[1], int(math.ceil(center_x)) + padding + 1)
    y0 = max(0, int(math.floor(center_y)) - padding)
    y1 = min(variance_u8.shape[0], int(math.ceil(center_y)) + padding + 1)
    local_variance = variance_u8[y0:y1, x0:x1]
    yy, xx = np.ogrid[y0:y1, x0:x1]
    distance = np.hypot(xx - center_x, yy - center_y)
    inner = local_variance[distance <= best_radius * 0.65]
    outer = local_variance[
        (distance >= best_radius * 1.12)
        & (distance <= best_radius * 1.45)
    ]
    if inner.size == 0 or outer.size == 0:
        return None
    inner_texture = float(np.median(inner))
    outer_texture = float(np.median(outer))
    texture_contrast = max(
        0.0,
        (outer_texture - inner_texture) / max(1.0, outer_texture),
    )
    # Hough 仅在纹理差确实指向“内平滑、外粗糙”时才能补圆。
    if texture_contrast < 0.06:
        return None

    coverage_score = min(1.0, best_coverage / 0.65)
    contrast_score = min(1.0, texture_contrast / 0.35)
    score = 0.55 * coverage_score + 0.45 * contrast_score
    return Circle(
        center_x=float(center_x),
        center_y=float(center_y),
        radius_px=best_radius,
        circularity=0.0,
        residual_ratio=0.0,
        score=float(score),
        source="hough_fallback",
        edge_coverage=best_coverage,
        texture_contrast=texture_contrast,
    )


def detect_texture_circles(
    image: np.ndarray,
    *,
    downsample: int,
    variance_window: int,
    open_kernel: int,
    close_kernel: int,
    min_radius_px: float,
    max_radius_px: float,
    min_center_distance_px: float,
    expected_count: int,
    min_circularity: float,
    max_residual_ratio: float,
    enable_hough_fallback: bool,
    hough_param1: float,
    hough_param2: float,
) -> DetectionDebug:
    """从低纹理平滑区域中提取圆，并将结果恢复为原图坐标。"""

    if downsample < 1:
        raise ValueError("downsample 必须至少为 1")
    for name, value in (
        ("variance-window", variance_window),
        ("open-kernel", open_kernel),
        ("close-kernel", close_kernel),
    ):
        require_odd_positive(name, value)
    if not 0 < min_radius_px < max_radius_px:
        raise ValueError("半径范围必须满足 0 < min-radius < max-radius")
    if expected_count < 1:
        raise ValueError("expected-count 必须至少为 1")

    timings: dict[str, float] = {}
    total_t0 = time.perf_counter()

    stage_t0 = time.perf_counter()
    gray = as_gray(image)
    if gray.dtype != np.uint8:
        gray = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    if downsample > 1:
        small = cv2.resize(
            gray,
            (
                max(1, round(gray.shape[1] / downsample)),
                max(1, round(gray.shape[0] / downsample)),
            ),
            interpolation=cv2.INTER_AREA,
        )
    else:
        small = gray
    timings["prepare_ms"] = (time.perf_counter() - stage_t0) * 1000.0

    stage_t0 = time.perf_counter()
    gray_float = small.astype(np.float32)
    local_mean = cv2.boxFilter(
        gray_float,
        cv2.CV_32F,
        (variance_window, variance_window),
        normalize=True,
    )
    local_sq_mean = cv2.boxFilter(
        gray_float * gray_float,
        cv2.CV_32F,
        (variance_window, variance_window),
        normalize=True,
    )
    variance = cv2.max(local_sq_mean - local_mean * local_mean, 0.0)
    variance_u8 = cv2.normalize(
        variance,
        None,
        0,
        255,
        cv2.NORM_MINMAX,
        cv2.CV_8U,
    )
    _threshold, mask = cv2.threshold(
        variance_u8,
        0,
        255,
        cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU,
    )
    threshold_mask = mask.copy()
    timings["texture_map_ms"] = (time.perf_counter() - stage_t0) * 1000.0

    stage_t0 = time.perf_counter()
    open_element = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE,
        (open_kernel, open_kernel),
    )
    close_element = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE,
        (close_kernel, close_kernel),
    )
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, open_element)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, close_element)
    timings["morphology_ms"] = (time.perf_counter() - stage_t0) * 1000.0

    stage_t0 = time.perf_counter()
    count, labels, stats, _centroids = cv2.connectedComponentsWithStats(
        mask,
        connectivity=8,
    )
    small_min_radius = min_radius_px / downsample
    small_max_radius = max_radius_px / downsample
    min_component_area = math.pi * small_min_radius * small_min_radius * 0.45
    max_component_area = math.pi * small_max_radius * small_max_radius * 1.55
    candidate_labels = []
    for label in range(1, count):
        x, y, width, height, area = stats[label]
        if _is_border_component(x, y, width, height, mask.shape[1], mask.shape[0]):
            continue
        if not min_component_area <= area <= max_component_area:
            continue
        aspect_ratio = width / max(1, height)
        if not 0.70 <= aspect_ratio <= 1.40:
            continue
        candidate_labels.append(label)
    timings["components_ms"] = (time.perf_counter() - stage_t0) * 1000.0

    stage_t0 = time.perf_counter()
    candidates: list[Circle] = []
    for label in candidate_labels:
        component_mask = np.where(labels == label, 255, 0).astype(np.uint8)
        contours, _hierarchy = cv2.findContours(
            component_mask,
            cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_NONE,
        )
        if not contours:
            continue
        contour = max(contours, key=cv2.contourArea)
        if len(contour) < 20:
            continue
        area = float(cv2.contourArea(contour))
        perimeter = float(cv2.arcLength(contour, True))
        if area <= 0 or perimeter <= 0:
            continue
        circularity = 4.0 * math.pi * area / (perimeter * perimeter)
        if circularity < min_circularity:
            continue
        center_x, center_y, radius = fit_circle_least_squares(contour)
        if not small_min_radius <= radius <= small_max_radius:
            continue
        points = contour.reshape(-1, 2).astype(np.float64)
        radial_distances = np.hypot(
            points[:, 0] - center_x,
            points[:, 1] - center_y,
        )
        residual_ratio = float(np.mean(np.abs(radial_distances - radius)) / radius)
        if residual_ratio > max_residual_ratio:
            continue
        radius_midpoint = (small_min_radius + small_max_radius) / 2.0
        radius_half_span = (small_max_radius - small_min_radius) / 2.0
        radius_score = max(0.0, 1.0 - abs(radius - radius_midpoint) / radius_half_span)
        residual_score = max(0.0, 1.0 - residual_ratio / max_residual_ratio)
        score = 0.60 * min(1.0, circularity) + 0.25 * residual_score + 0.15 * radius_score
        candidates.append(Circle(
            center_x=center_x * downsample,
            center_y=center_y * downsample,
            radius_px=radius * downsample,
            circularity=float(circularity),
            residual_ratio=residual_ratio,
            score=float(score),
            source="texture",
        ))
    candidates = _deduplicate(candidates, min_center_distance_px)
    timings["candidate_fit_ms"] = (time.perf_counter() - stage_t0) * 1000.0

    # 纹理连通域在靠近平滑背景时可能粘连到图像边缘。仅在缺圆时才运行
    # Hough 补检，且 Hough 只提供圆心候选，半径仍由局部圆周梯度精修。
    stage_t0 = time.perf_counter()
    if enable_hough_fallback and len(candidates) < expected_count:
        blurred = cv2.GaussianBlur(small, (5, 5), 1.2)
        hough_min_radius = max(2, round(small_min_radius * 0.80))
        hough_max_radius = max(
            hough_min_radius + 1,
            round(small_max_radius * 0.80),
        )
        hough_min_distance = max(
            small_max_radius * 2.0,
            min_center_distance_px / downsample,
        )
        raw_hough = cv2.HoughCircles(
            blurred,
            cv2.HOUGH_GRADIENT,
            dp=1.2,
            minDist=hough_min_distance,
            param1=hough_param1,
            param2=hough_param2,
            minRadius=hough_min_radius,
            maxRadius=hough_max_radius,
        )
        timings["hough_detect_ms"] = (time.perf_counter() - stage_t0) * 1000.0
        refine_t0 = time.perf_counter()
        gradient_x = cv2.Sobel(
            blurred,
            cv2.CV_32F,
            1,
            0,
            ksize=3,
        )
        gradient_y = cv2.Sobel(
            blurred,
            cv2.CV_32F,
            0,
            1,
            ksize=3,
        )
        gradient = cv2.magnitude(gradient_x, gradient_y)
        global_edge_threshold = float(np.percentile(gradient, 75.0))
        if raw_hough is not None:
            for center_x, center_y, _radius in raw_hough[0]:
                full_center_x = float(center_x) * downsample
                full_center_y = float(center_y) * downsample
                if any(
                    math.hypot(
                        full_center_x - existing.center_x,
                        full_center_y - existing.center_y,
                    ) < min_center_distance_px
                    for existing in candidates
                ):
                    # 该圆已经由纹理分割稳定检出，Hough 不再重复精修。
                    continue
                refined = _refine_hough_candidate(
                    gradient,
                    variance_u8,
                    global_edge_threshold,
                    float(center_x),
                    float(center_y),
                    small_min_radius,
                    small_max_radius,
                )
                if refined is not None:
                    refined.center_x *= downsample
                    refined.center_y *= downsample
                    refined.radius_px *= downsample
                    candidates.append(refined)
        candidates = _deduplicate(candidates, min_center_distance_px)
        timings["hough_refine_ms"] = (time.perf_counter() - refine_t0) * 1000.0
    else:
        timings["hough_detect_ms"] = 0.0
        timings["hough_refine_ms"] = 0.0
    timings["hough_fallback_ms"] = (time.perf_counter() - stage_t0) * 1000.0
    candidates = candidates[:expected_count]
    candidates.sort(key=lambda item: (item.center_x, item.center_y))
    timings["total_ms"] = (time.perf_counter() - total_t0) * 1000.0
    return DetectionDebug(
        texture_map=variance_u8,
        threshold_mask=threshold_mask,
        mask=mask,
        candidates=candidates,
        timings_ms=timings,
        connected_component_count=count - 1,
        preselected_component_count=len(candidate_labels),
    )


def make_overlay(image: np.ndarray, circles: list[Circle]) -> np.ndarray:
    if image.ndim == 2:
        output = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    elif image.ndim == 3 and image.shape[2] == 1:
        output = cv2.cvtColor(image[:, :, 0], cv2.COLOR_GRAY2BGR)
    elif image.ndim == 3 and image.shape[2] == 4:
        output = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
    else:
        output = image.copy()

    thickness = max(1, round(min(output.shape[:2]) / 1000))
    font_scale = max(0.45, min(0.9, min(output.shape[:2]) / 1400))
    for index, circle in enumerate(circles, start=1):
        center = (round(circle.center_x), round(circle.center_y))
        radius = max(1, round(circle.radius_px))
        cv2.circle(output, center, radius, (0, 255, 0), thickness, cv2.LINE_AA)
        cv2.drawMarker(
            output,
            center,
            (0, 0, 255),
            cv2.MARKER_CROSS,
            thickness * 8,
            thickness,
            cv2.LINE_AA,
        )
        text = f"#{index} R={circle.radius_px:.1f} S={circle.score:.3f}"
        cv2.putText(
            output,
            text,
            (center[0] - radius, max(20, center[1] - radius - 10)),
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            (0, 0, 255),
            thickness,
            cv2.LINE_AA,
        )
    return output


def write_image(path: Path, image: np.ndarray):
    path.parent.mkdir(parents=True, exist_ok=True)
    extension = path.suffix.lower() or ".jpg"
    if extension == ".jpeg":
        extension = ".jpg"
    if extension not in {".jpg", ".png", ".bmp"}:
        raise ValueError("输出图仅支持 jpg、png 或 bmp")
    options = [cv2.IMWRITE_JPEG_QUALITY, 95] if extension == ".jpg" else []
    ok, encoded = cv2.imencode(extension, image, options)
    if not ok:
        raise ValueError("OpenCV 无法编码输出图")
    encoded.tofile(str(path))


def percentile(values: list[float], ratio: float) -> float:
    return float(np.percentile(np.asarray(values, dtype=np.float64), ratio))


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    image_path = Path(args.image).expanduser().resolve()
    if not image_path.is_file():
        print(f"[错误] 输入图像不存在: {image_path}", file=sys.stderr)
        return 2
    if args.warmup < 0 or args.runs < 1:
        print("[错误] warmup 必须不小于 0，runs 必须至少为 1", file=sys.stderr)
        return 2

    try:
        image = read_image(image_path)
        keyword_args = dict(
            downsample=args.downsample,
            variance_window=args.variance_window,
            open_kernel=args.open_kernel,
            close_kernel=args.close_kernel,
            min_radius_px=args.min_radius,
            max_radius_px=args.max_radius,
            min_center_distance_px=args.min_center_distance,
            expected_count=args.expected_count,
            min_circularity=args.min_circularity,
            max_residual_ratio=args.max_residual_ratio,
            enable_hough_fallback=not args.no_hough_fallback,
            hough_param1=args.hough_param1,
            hough_param2=args.hough_param2,
        )
        for _ in range(args.warmup):
            detect_texture_circles(image, **keyword_args)
        results = [
            detect_texture_circles(image, **keyword_args)
            for _ in range(args.runs)
        ]
    except (OSError, ValueError, cv2.error) as error:
        print(f"[错误] {error}", file=sys.stderr)
        return 2

    latest = results[-1]
    elapsed = [item.timings_ms["total_ms"] for item in results]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    output_dir = (
        Path(args.output_dir).expanduser().resolve()
        if args.output_dir
        else PROJECT_ROOT / "output" / "texture_circle_tests" / timestamp
    )
    try:
        write_image(
            output_dir / f"{image_path.stem}_texture_result.jpg",
            make_overlay(image, latest.candidates),
        )
        write_image(
            output_dir / f"{image_path.stem}_texture_mask.png",
            latest.mask,
        )
        write_image(
            output_dir / f"{image_path.stem}_texture_map.png",
            latest.texture_map,
        )
        write_image(
            output_dir / f"{image_path.stem}_threshold_mask.png",
            latest.threshold_mask,
        )
    except (OSError, ValueError, cv2.error) as error:
        print(f"[错误] 保存调试图失败: {error}", file=sys.stderr)
        return 2

    print(f"输入图像: {image_path}")
    print(f"图像尺寸: {image.shape[1]} x {image.shape[0]}")
    print(
        f"测试参数: downsample={args.downsample}, "
        f"radius={args.min_radius:g}~{args.max_radius:g}px, "
        f"expected={args.expected_count}, runs={args.runs}"
    )
    print(f"候选圆数量: {len(latest.candidates)}")
    print(
        "连通域统计: "
        f"全部={latest.connected_component_count}，"
        f"面积/长宽比预筛后={latest.preselected_component_count}"
    )
    for index, circle in enumerate(latest.candidates, start=1):
        print(
            f"  circle-{index:03d}: "
            f"center=({circle.center_x:.1f}, {circle.center_y:.1f}), "
            f"radius={circle.radius_px:.1f}px, "
            f"circularity={circle.circularity:.3f}, "
            f"residual={circle.residual_ratio:.4f}, score={circle.score:.3f}, "
            f"source={circle.source}, edge={circle.edge_coverage:.3f}, "
            f"texture={circle.texture_contrast:.3f}"
        )
    print("CT（仅算法，不含读写图）:")
    for key, value in latest.timings_ms.items():
        print(f"  {key}: {value:.2f} ms")
    print(
        f"基准 {args.runs} 次: "
        f"P50={percentile(elapsed, 50):.2f} ms, "
        f"P95={percentile(elapsed, 95):.2f} ms, "
        f"max={max(elapsed):.2f} ms"
    )
    print("100ms 验收: " + ("通过" if percentile(elapsed, 95) <= 100.0 else "未通过"))
    print(f"结果目录: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
