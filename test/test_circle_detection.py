# -*- coding: utf-8 -*-
"""轮廓多圆检测、筛选、选择和整体确认测试。"""

import ast
import unittest
from pathlib import Path

import cv2
import numpy as np

from backend.circle_detection import (
    ContourCircleDetector,
    HoughCircleDetector,
    _deduplicate_candidates,
)
from backend.inspection_config import CircleDetectionConfig
from backend.inspection_types import CircleCandidate


class ContourCircleDetectorTest(unittest.TestCase):
    def setUp(self):
        self.detector = ContourCircleDetector()

    @staticmethod
    def config(**overrides):
        values = {
            "downsample_factor": 1,
            "blur_kernel_size": 9,
            "min_center_distance_px": 70.0,
            "min_radius_px": 30,
            "max_radius_px": 60,
            "expected_circle_count": 1,
            "min_candidate_score": 0.5,
        }
        values.update(overrides)
        return CircleDetectionConfig(**values)

    @staticmethod
    def dark_disk_image(width=600, height=360, circles=()):
        x_gradient = np.linspace(175, 225, width, dtype=np.float32)
        image = np.tile(x_gradient, (height, 1))
        rng = np.random.default_rng(20260902)
        image += rng.normal(0.0, 4.0, image.shape)
        image = np.clip(image, 0, 255).astype(np.uint8)
        for center_x, center_y, radius in circles:
            cv2.circle(image, (center_x, center_y), radius, 75, -1)
            cv2.circle(image, (center_x, center_y), radius, 55, 2)
        return image

    def test_detects_multiple_dark_disks_on_uneven_background(self):
        image = self.dark_disk_image(
            circles=((100, 220, 42), (300, 215, 44), (500, 210, 40)),
        )

        candidates, selected, confirmed, warnings = self.detector.detect(
            image,
            self.config(expected_circle_count=3),
        )

        self.assertEqual(len(candidates), 3)
        self.assertEqual([round(item.center_x) for item in candidates], [100, 300, 500])
        self.assertTrue(confirmed)
        self.assertEqual(warnings, [])
        self.assertIsNotNone(selected)
        self.assertTrue(all(item.source == "contour" for item in candidates))

    def test_candidates_are_position_sorted_but_best_remains_selected(self):
        image = self.dark_disk_image(
            circles=((100, 220, 36), (430, 210, 45)),
        )

        candidates, selected, confirmed, _warnings = self.detector.detect(
            image,
            self.config(expected_circle_count=2),
        )

        self.assertEqual(len(candidates), 2)
        self.assertLess(candidates[0].center_x, candidates[1].center_x)
        self.assertEqual(
            candidates[selected].score,
            max(item.score for item in candidates),
        )
        self.assertTrue(confirmed)

    def test_downsampled_coordinates_are_restored_to_original_image(self):
        image = self.dark_disk_image(
            width=1200,
            height=1000,
            circles=((720, 520, 160),),
        )

        candidates, selected, confirmed, warnings = self.detector.detect(
            image,
            self.config(
                downsample_factor=4,
                min_radius_px=130,
                max_radius_px=190,
                min_center_distance_px=250.0,
            ),
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(selected, 0)
        self.assertTrue(confirmed)
        self.assertEqual(warnings, [])
        self.assertAlmostEqual(candidates[0].center_x, 720, delta=8)
        self.assertAlmostEqual(candidates[0].center_y, 520, delta=8)
        self.assertAlmostEqual(candidates[0].radius_px, 160, delta=12)

    def test_fewer_candidates_than_expected_returns_warning(self):
        image = self.dark_disk_image(circles=((300, 210, 42),))

        candidates, selected, confirmed, warnings = self.detector.detect(
            image,
            self.config(expected_circle_count=3),
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(selected, 0)
        self.assertFalse(confirmed)
        self.assertTrue(any("预期检测到 3" in item for item in warnings))
        self.assertTrue(any("轮廓去重后检测到 1" in item for item in warnings))

    def test_low_circularity_score_prevents_automatic_confirmation(self):
        image = self.dark_disk_image(circles=((300, 210, 42),))

        candidates, selected, confirmed, warnings = self.detector.detect(
            image,
            self.config(min_candidate_score=0.99),
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(selected, 0)
        self.assertFalse(confirmed)
        self.assertTrue(any("圆度评分" in item for item in warnings))

    def test_border_object_and_small_noise_are_rejected(self):
        image = self.dark_disk_image(circles=((0, 200, 45),))
        cv2.circle(image, (300, 150), 8, 60, -1)

        candidates, selected, confirmed, warnings = self.detector.detect(
            image,
            self.config(),
        )

        self.assertEqual(candidates, [])
        self.assertIsNone(selected)
        self.assertFalse(confirmed)
        self.assertTrue(any("未找到候选圆" in item for item in warnings))

    def test_nearby_candidates_are_deduplicated_with_high_score_kept(self):
        candidates = [
            CircleCandidate(100.0, 100.0, 40.0, 0.9, "contour"),
            CircleCandidate(110.0, 105.0, 41.0, 0.6, "contour"),
            CircleCandidate(280.0, 100.0, 40.0, 0.8, "contour"),
        ]

        kept = _deduplicate_candidates(
            candidates,
            min_center_distance_px=50.0,
        )

        self.assertEqual(len(kept), 2)
        self.assertEqual([item.center_x for item in kept], [100.0, 280.0])

    def test_legacy_class_name_uses_contour_implementation(self):
        detector = HoughCircleDetector()
        image = self.dark_disk_image(circles=((300, 210, 42),))

        candidates, _selected, _confirmed, _warnings = detector.detect(
            image,
            self.config(),
        )

        self.assertEqual(candidates[0].source, "contour")

    def test_invalid_images_and_config_raise_clear_errors(self):
        with self.assertRaisesRegex(ValueError, "空图像"):
            self.detector.detect(None, self.config())
        with self.assertRaisesRegex(ValueError, "只支持"):
            self.detector.detect(np.zeros((2, 2, 2, 2)), self.config())
        with self.assertRaisesRegex(ValueError, "预期圆数量"):
            self.detector.detect(
                np.zeros((20, 20), dtype=np.uint8),
                self.config(expected_circle_count=0),
            )

    def test_module_does_not_import_gui_or_model_libraries_or_call_hough(self):
        module_path = (
            Path(__file__).resolve().parents[1]
            / "backend"
            / "circle_detection.py"
        )
        source = module_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported_roots = set()

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(
                    alias.name.split(".", 1)[0]
                    for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".", 1)[0])

        forbidden = {"PyQt5", "torch", "ultralytics"}
        self.assertTrue(forbidden.isdisjoint(imported_roots))
        self.assertNotIn("cv2.HoughCircles", source)


if __name__ == "__main__":
    unittest.main()
