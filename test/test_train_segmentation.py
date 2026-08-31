# -*- coding: utf-8 -*-
"""训练脚本参数测试；不执行任何真实训练。"""

import tempfile
import unittest
from pathlib import Path

from tools.train_segmentation import build_train_arguments, parse_args


class TrainSegmentationScriptTest(unittest.TestCase):
    def test_smoke_mode_reduces_training_work_without_running_training(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / "data.yaml"
            data.write_text("names: {}\n", encoding="utf-8")
            args = parse_args([
                "--data", str(data),
                "--epochs", "100",
                "--batch", "8",
                "--workers", "4",
                "--smoke",
            ])
            values = build_train_arguments(args)

        self.assertEqual(values["epochs"], 1)
        self.assertEqual(values["batch"], 2)
        self.assertEqual(values["workers"], 0)
        self.assertEqual(values["imgsz"], 1280)

    def test_missing_data_yaml_is_rejected_before_model_import(self):
        args = parse_args(["--data", "missing-data.yaml"])
        with self.assertRaises(FileNotFoundError):
            build_train_arguments(args)


if __name__ == "__main__":
    unittest.main()
