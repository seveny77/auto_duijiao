# -*- coding: utf-8 -*-
"""对单张真实图像运行当前轮廓找圆算法，并保存可视化结果。

示例：
    python -B tools/test_circle_detection_image.py D:\\scan\\sample.jpg
    python -B tools/test_circle_detection_image.py D:\\scan\\sample.jpg \
        --min-radius 250 --max-radius 300 --expected-count 5
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.circle_detection import ContourCircleDetector
from backend.inspection_config import (
    CircleDetectionConfig,
    InspectionConfigStore,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="对真实图像运行轮廓找圆，并保存带标注的测试结果图。"
    )
    parser.add_argument("image", help="待测试的输入图像路径")
    parser.add_argument(
        "--config",
        default=str(PROJECT_ROOT / "gui" / "inspection_config.json"),
        help="检测配置 JSON 路径（默认：gui/inspection_config.json）",
    )
    parser.add_argument(
        "--output",
        default="",
        help=(
            "结果图路径；省略时保存至 output/circle_tests，"
            "文件名自动附带时间戳"
        ),
    )
    parser.add_argument("--downsample", type=int, help="覆盖找圆降采样倍率")
    parser.add_argument("--blur", type=int, help="覆盖高斯模糊核尺寸（正奇数）")
    parser.add_argument("--min-center-distance", type=float, help="覆盖候选圆心最小距离")
    parser.add_argument("--min-radius", type=int, help="覆盖候选圆最小半径（原图像素）")
    parser.add_argument("--max-radius", type=int, help="覆盖候选圆最大半径（原图像素）")
    parser.add_argument("--expected-count", type=int, help="覆盖预期圆数量")
    parser.add_argument("--min-score", type=float, help="覆盖自动确认最低圆度评分")
    return parser


def read_image(path: Path):
    """支持含中文的 Windows 路径，并保持图像原始通道数。"""

    try:
        raw = np.fromfile(str(path), dtype=np.uint8)
    except OSError as error:
        raise ValueError(f"无法读取输入文件: {error}") from error

    image = cv2.imdecode(raw, cv2.IMREAD_UNCHANGED)
    if image is None or image.size == 0:
        raise ValueError("OpenCV 无法解码该图像")
    return image


def load_circle_config(config_path: Path) -> CircleDetectionConfig:
    """读取独立检测配置；配置不存在时仍允许用默认找圆参数测试。"""

    if not config_path.exists():
        print(f"[提示] 配置文件不存在，使用默认找圆参数: {config_path}")
        return CircleDetectionConfig()
    return InspectionConfigStore(str(config_path)).load().circle


def apply_overrides(config: CircleDetectionConfig, args) -> CircleDetectionConfig:
    """只覆盖命令行明确提供的参数。"""

    values = {
        "downsample_factor": args.downsample,
        "blur_kernel_size": args.blur,
        "min_center_distance_px": args.min_center_distance,
        "min_radius_px": args.min_radius,
        "max_radius_px": args.max_radius,
        "expected_circle_count": args.expected_count,
        "min_candidate_score": args.min_score,
    }
    for field_name, value in values.items():
        if value is not None:
            setattr(config, field_name, value)
    return config


def make_overlay(image, circles, selected_index):
    """将候选圆绘制在图像副本上，不修改输入图。"""

    if image.ndim == 2:
        overlay = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    elif image.ndim == 3 and image.shape[2] == 1:
        overlay = cv2.cvtColor(image[:, :, 0], cv2.COLOR_GRAY2BGR)
    elif image.ndim == 3 and image.shape[2] == 4:
        overlay = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
    else:
        overlay = image.copy()

    thickness = max(1, round(min(overlay.shape[:2]) / 900))
    font_scale = max(0.45, min(0.9, min(overlay.shape[:2]) / 1200))
    for index, circle in enumerate(circles):
        center = (round(circle.center_x), round(circle.center_y))
        radius = max(1, round(circle.radius_px))
        is_best = index == selected_index
        color = (0, 215, 255) if is_best else (0, 255, 0)
        cv2.circle(overlay, center, radius, color, thickness, cv2.LINE_AA)
        cv2.circle(overlay, center, max(2, thickness + 1), color, -1, cv2.LINE_AA)
        label = f"circle-{index + 1:03d}  r={circle.radius_px:.1f}  s={circle.score:.3f}"
        label_position = (center[0] - radius, max(20, center[1] - radius - 8))
        cv2.putText(
            overlay,
            label,
            label_position,
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            color,
            thickness,
            cv2.LINE_AA,
        )
    return overlay


def resolve_output_path(raw_path: str, image_path: Path) -> Path:
    if raw_path:
        output = Path(raw_path).expanduser()
        if not output.is_absolute():
            output = (Path.cwd() / output).resolve()
        if not output.suffix:
            output = output.with_suffix(".jpg")
        return output

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    return (
        PROJECT_ROOT
        / "output"
        / "circle_tests"
        / f"{image_path.stem}_circles_{timestamp}.jpg"
    )


def write_image(path: Path, image):
    suffix = path.suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".bmp"}:
        raise ValueError("输出文件仅支持 .jpg、.png 或 .bmp")

    extension = ".jpg" if suffix in {".jpg", ".jpeg"} else suffix
    options = [cv2.IMWRITE_JPEG_QUALITY, 95] if extension == ".jpg" else []
    ok, encoded = cv2.imencode(extension, image, options)
    if not ok:
        raise ValueError("OpenCV 无法编码结果图")
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded.tofile(str(path))


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    image_path = Path(args.image).expanduser().resolve()
    if not image_path.is_file():
        print(f"[错误] 输入图像不存在: {image_path}", file=sys.stderr)
        return 2

    try:
        image = read_image(image_path)
        config_path = Path(args.config).expanduser().resolve()
        config = apply_overrides(load_circle_config(config_path), args)
        detector = ContourCircleDetector()
        circles, selected_index, confirmed, warnings = detector.detect(
            image,
            config,
        )
        output_path = resolve_output_path(args.output, image_path)
        write_image(output_path, make_overlay(image, circles, selected_index))
    except (OSError, ValueError) as error:
        print(f"[错误] {error}", file=sys.stderr)
        return 2

    print(f"输入图像: {image_path}")
    print(f"图像尺寸: {image.shape[1]} x {image.shape[0]}")
    print(
        "有效参数: "
        f"downsample={config.downsample_factor}, "
        f"blur={config.blur_kernel_size}, "
        f"radius={config.min_radius_px}~{config.max_radius_px}px, "
        f"min_center_distance={config.min_center_distance_px}px, "
        f"expected_count={config.expected_circle_count}, "
        f"min_score={config.min_candidate_score}"
    )
    print(f"找到候选圆: {len(circles)} 个；自动确认: {'是' if confirmed else '否'}")
    for index, circle in enumerate(circles, start=1):
        marker = " [最高评分]" if index - 1 == selected_index else ""
        print(
            f"  circle-{index:03d}: "
            f"center=({circle.center_x:.1f}, {circle.center_y:.1f}), "
            f"radius={circle.radius_px:.1f}px, score={circle.score:.3f}"
            f"{marker}"
        )
    for warning in warnings:
        print(f"[警告] {warning}")
    print(f"结果图: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
