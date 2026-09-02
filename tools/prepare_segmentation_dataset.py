# -*- coding: utf-8 -*-
"""把 img/ + label/ LabelMe 数据随机转换成 YOLO-Seg 数据集。"""

import argparse
import json
import math
import random
import shutil
from collections import Counter
from pathlib import Path


DEFAULT_CLASSES = ["异物", "脏污"]
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="将 LabelMe polygon 标注随机划分并转换为 YOLO-Seg",
    )
    parser.add_argument("--source", required=True, help="包含 img/ 和 label/ 的目录")
    parser.add_argument("--output", required=True, help="新建的 YOLO-Seg 输出目录")
    parser.add_argument("--classes", nargs="+", default=DEFAULT_CLASSES)
    parser.add_argument("--train-ratio", type=float, default=0.8)
    parser.add_argument("--val-ratio", type=float, default=0.1)
    parser.add_argument("--test-ratio", type=float, default=0.1)
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args(argv)


def prepare_dataset(
    source,
    output,
    *,
    classes=None,
    train_ratio=0.8,
    val_ratio=0.1,
    test_ratio=0.1,
    seed=42,
):
    """转换数据并返回写入的 dataset_summary 字典。"""

    source = Path(source).resolve()
    output = Path(output).resolve()
    image_root = source / "img"
    annotation_root = source / "label"
    class_names = list(classes or DEFAULT_CLASSES)
    _validate_inputs(
        source,
        output,
        image_root,
        annotation_root,
        class_names,
        train_ratio,
        val_ratio,
        test_ratio,
    )

    images = sorted(
        item for item in image_root.iterdir()
        if item.is_file() and item.suffix.lower() in IMAGE_EXTENSIONS
    )
    if not images:
        raise ValueError(f"没有找到训练图片: {image_root}")

    annotations = {item.stem: item for item in annotation_root.glob("*.json")}
    image_by_stem = {item.stem: item for item in images}
    # 没有同名 JSON 的图片按“无缺陷负样本”处理：输出空 YOLO 标签。
    # 不修改源目录，也不为源图片伪造 LabelMe JSON。
    missing_annotations = sorted(set(image_by_stem) - set(annotations))
    missing_images = sorted(set(annotations) - set(image_by_stem))
    if missing_images:
        raise ValueError(f"JSON 缺少同名图片: {missing_images[:5]}")

    assignments = _random_split(
        sorted(image_by_stem),
        train_ratio,
        val_ratio,
        seed,
    )
    class_to_id = {name: index for index, name in enumerate(class_names)}
    class_counts = Counter()
    split_counts = Counter(assignments.values())

    for split in ("train", "val", "test"):
        (output / "images" / split).mkdir(parents=True, exist_ok=False)
        (output / "labels" / split).mkdir(parents=True, exist_ok=False)

    for stem in sorted(image_by_stem):
        image_path = image_by_stem[stem]
        annotation_path = annotations.get(stem)
        split = assignments[stem]
        if annotation_path is None:
            # 空 txt 是 YOLO 标准的负样本表示：图片存在，但没有任何实例。
            lines, counts = [], Counter()
        else:
            lines, counts = _convert_annotation(
                annotation_path,
                image_path,
                class_to_id,
            )
        class_counts.update(counts)
        shutil.copy2(image_path, output / "images" / split / image_path.name)
        label_path = output / "labels" / split / f"{stem}.txt"
        label_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

    classes_payload = {
        name: class_id for name, class_id in class_to_id.items()
    }
    manifest = {
        "seed": int(seed),
        "train_ratio": float(train_ratio),
        "val_ratio": float(val_ratio),
        "test_ratio": float(test_ratio),
        "assignments": assignments,
    }
    summary = {
        "source": str(source),
        "output": str(output),
        "image_count": len(images),
        "split_counts": dict(split_counts),
        "class_counts": {
            name: class_counts.get(name, 0) for name in class_names
        },
        "unannotated_image_count": len(missing_annotations),
    }
    _write_json(output / "classes.json", classes_payload)
    _write_json(output / "split_manifest.json", manifest)
    _write_json(output / "dataset_summary.json", summary)
    _write_data_yaml(output / "data.yaml", output, class_names)
    return summary


def _validate_inputs(
    source,
    output,
    image_root,
    annotation_root,
    class_names,
    train_ratio,
    val_ratio,
    test_ratio,
):
    if not source.is_dir():
        raise FileNotFoundError(f"源数据目录不存在: {source}")
    if not image_root.is_dir() or not annotation_root.is_dir():
        raise ValueError("源数据目录必须包含 img/ 和 label/")
    if output.exists():
        raise FileExistsError(f"输出目录已存在，请使用新目录: {output}")
    if not class_names or any(not str(name).strip() for name in class_names):
        raise ValueError("类别名称不能为空")
    if len(set(class_names)) != len(class_names):
        raise ValueError("类别名称不能重复")
    ratios = (train_ratio, val_ratio, test_ratio)
    if any(not math.isfinite(value) or value <= 0 for value in ratios):
        raise ValueError("train/val/test 比例必须全部大于 0")
    if not math.isclose(sum(ratios), 1.0, abs_tol=1e-9):
        raise ValueError("train/val/test 比例之和必须为 1")


def _random_split(stems, train_ratio, val_ratio, seed):
    if len(stems) < 3:
        raise ValueError("随机划分至少需要 3 张图片")
    shuffled = list(stems)
    random.Random(seed).shuffle(shuffled)
    count = len(shuffled)
    train_count = max(1, int(round(count * train_ratio)))
    val_count = max(1, int(round(count * val_ratio)))
    if train_count + val_count >= count:
        train_count = max(1, count - 2)
        val_count = 1
    assignments = {}
    for index, stem in enumerate(shuffled):
        if index < train_count:
            split = "train"
        elif index < train_count + val_count:
            split = "val"
        else:
            split = "test"
        assignments[stem] = split
    return dict(sorted(assignments.items()))


def _convert_annotation(annotation_path, image_path, class_to_id):
    import cv2
    import numpy as np

    payload = json.loads(annotation_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("shapes"), list):
        raise ValueError(f"LabelMe JSON 结构无效: {annotation_path}")

    image = cv2.imdecode(np.fromfile(str(image_path), dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    if image is None:
        raise ValueError(f"图片无法解码: {image_path}")
    height, width = image.shape[:2]
    if (payload.get("imageWidth"), payload.get("imageHeight")) != (width, height):
        raise ValueError(f"JSON 与图片尺寸不一致: {annotation_path.name}")

    lines = []
    counts = Counter()
    for index, shape in enumerate(payload["shapes"]):
        if shape.get("shape_type") != "polygon":
            raise ValueError(
                f"{annotation_path.name} shapes[{index}] 不是 polygon"
            )
        label = str(shape.get("label", ""))
        if label not in class_to_id:
            raise ValueError(
                f"{annotation_path.name} 出现未配置类别: {label}"
            )
        points = shape.get("points")
        if not isinstance(points, list) or len(points) < 3:
            raise ValueError(
                f"{annotation_path.name} shapes[{index}] 少于3个点"
            )
        coordinates = []
        for point in points:
            if not isinstance(point, list) or len(point) != 2:
                raise ValueError(
                    f"{annotation_path.name} shapes[{index}] 点格式无效"
                )
            x_value, y_value = float(point[0]), float(point[1])
            if not (
                math.isfinite(x_value)
                and math.isfinite(y_value)
                and 0 <= x_value <= width
                and 0 <= y_value <= height
            ):
                raise ValueError(
                    f"{annotation_path.name} shapes[{index}] 坐标越界"
                )
            coordinates.extend((x_value / width, y_value / height))
        line = [str(class_to_id[label])]
        line.extend(f"{value:.8f}" for value in coordinates)
        lines.append(" ".join(line))
        counts[label] += 1
    return lines, counts


def _write_json(path, payload):
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_data_yaml(path, output, class_names):
    lines = [
        f"path: {output.as_posix()}",
        "train: images/train",
        "val: images/val",
        "test: images/test",
        "names:",
    ]
    lines.extend(
        f"  {index}: {json.dumps(name, ensure_ascii=False)}"
        for index, name in enumerate(class_names)
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv=None):
    args = parse_args(argv)
    summary = prepare_dataset(
        args.source,
        args.output,
        classes=args.classes,
        train_ratio=args.train_ratio,
        val_ratio=args.val_ratio,
        test_ratio=args.test_ratio,
        seed=args.seed,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
