# -*- coding: utf-8 -*-
"""LabelMe 随机转换 YOLO-Seg 数据集测试。"""

import json
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from tools.prepare_segmentation_dataset import prepare_dataset


class PrepareSegmentationDatasetTest(unittest.TestCase):
    def _create_source(self, root, count=10):
        source = root / "source"
        image_root = source / "img"
        label_root = source / "label"
        image_root.mkdir(parents=True)
        label_root.mkdir()
        for index in range(count):
            stem = f"image_{index:02d}"
            image = np.zeros((80, 100, 3), dtype=np.uint8)
            cv2.imwrite(str(image_root / f"{stem}.jpg"), image)
            payload = {
                "shapes": [{
                    "label": "异物" if index % 2 == 0 else "脏污",
                    "shape_type": "polygon",
                    "points": [[10, 10], [30, 10], [30, 30], [10, 30]],
                }],
                "imagePath": f"{stem}.jpg",
                "imageWidth": 100,
                "imageHeight": 80,
            }
            (label_root / f"{stem}.json").write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
        return source

    def test_conversion_is_random_but_reproducible_and_writes_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self._create_source(root)
            first = root / "first"
            second = root / "second"
            summary = prepare_dataset(source, first, seed=7)
            prepare_dataset(source, second, seed=7)

            first_manifest = json.loads(
                (first / "split_manifest.json").read_text(encoding="utf-8")
            )
            second_manifest = json.loads(
                (second / "split_manifest.json").read_text(encoding="utf-8")
            )

            self.assertEqual(
                first_manifest["assignments"],
                second_manifest["assignments"],
            )
            self.assertEqual(summary["image_count"], 10)
            self.assertEqual(summary["split_counts"], {
                "train": 8,
                "val": 1,
                "test": 1,
            })
            self.assertEqual(summary["class_counts"], {
                "异物": 5,
                "脏污": 5,
            })
            label_files = list((first / "labels").rglob("*.txt"))
            self.assertEqual(len(label_files), 10)
            tokens = label_files[0].read_text(encoding="utf-8").split()
            self.assertEqual(len(tokens), 9)
            self.assertTrue((first / "data.yaml").is_file())
            self.assertTrue((first / "classes.json").is_file())

    def test_existing_output_and_unknown_class_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self._create_source(root, count=3)
            output = root / "exists"
            output.mkdir()
            with self.assertRaises(FileExistsError):
                prepare_dataset(source, output)

            payload_path = source / "label" / "image_00.json"
            payload = json.loads(payload_path.read_text(encoding="utf-8"))
            payload["shapes"][0]["label"] = "未知类别"
            payload_path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "未配置类别"):
                prepare_dataset(source, root / "new_output")


if __name__ == "__main__":
    unittest.main()
