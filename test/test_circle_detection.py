# -*- coding: utf-8 -*-
"""Hough 圆检测与最高分候选选择测试。"""

import ast
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from backend.circle_detection import HoughCircleDetector
from backend.inspection_config import CircleDetectionConfig


class HoughCircleDetectorTest(unittest.TestCase):
    def setUp(self):
        self.detector = HoughCircleDetector()

    @staticmethod
    def config(**overrides):
        values = {
            "downsample_factor": 1,
            "blur_kernel_size": 5,
            "hough_dp": 1.2,
            "hough_param1": 100.0,
            "hough_param2": 25.0,
            "min_center_distance_px": 50.0,
            "min_radius_px": 70,
            "max_radius_px": 90,
            "expected_circle_count": 1,
            "min_candidate_score": 0.1,
        }
        values.update(overrides)
        return CircleDetectionConfig(**values)

    def test_real_hough_detects_synthetic_circle(self):
        image = np.zeros((400, 400), dtype=np.uint8)
        cv2.circle(image, (210, 180), 80, 255, 3)

        candidates, selected, confirmed, warnings = self.detector.detect(
            image,
            self.config(),
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(selected, 0)
        self.assertTrue(confirmed)
        self.assertAlmostEqual(candidates[0].center_x, 210, delta=5)
        self.assertAlmostEqual(candidates[0].center_y, 180, delta=5)
        self.assertAlmostEqual(candidates[0].radius_px, 80, delta=5)
        self.assertEqual(candidates[0].source, "hough")

    def test_highest_edge_support_is_selected_and_count_is_limited(self):
        image = np.zeros((300, 300), dtype=np.uint8)
        cv2.circle(image, (80, 100), 40, 255, 3)
        raw = np.array([[[80.0, 100.0, 40.0], [220.0, 100.0, 40.0]]])

        with patch("cv2.HoughCircles", return_value=raw):
            candidates, selected, confirmed, warnings = self.detector.detect(
                image,
                self.config(
                    min_radius_px=30,
                    max_radius_px=50,
                    expected_circle_count=1,
                    min_candidate_score=0.0,
                ),
            )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(selected, 0)
        self.assertTrue(confirmed)
        self.assertAlmostEqual(candidates[0].center_x, 80.0)
        self.assertTrue(any("实际检测到 2" in item for item in warnings))

    def test_downsampled_coordinates_are_restored_to_original_image(self):
        image = np.zeros((400, 400), dtype=np.uint8)
        raw = np.array([[[50.0, 40.0, 20.0]]])

        with patch("cv2.HoughCircles", return_value=raw):
            candidates, selected, confirmed, warnings = self.detector.detect(
                image,
                self.config(
                    downsample_factor=4,
                    min_radius_px=60,
                    max_radius_px=100,
                    min_candidate_score=0.0,
                ),
            )

        self.assertEqual(selected, 0)
        self.assertAlmostEqual(candidates[0].center_x, 200.0)
        self.assertAlmostEqual(candidates[0].center_y, 160.0)
        self.assertAlmostEqual(candidates[0].radius_px, 80.0)

    def test_fewer_candidates_than_expected_returns_warning(self):
        image = np.zeros((200, 200), dtype=np.uint8)
        raw = np.array([[[100.0, 100.0, 40.0]]])

        with patch("cv2.HoughCircles", return_value=raw):
            candidates, selected, confirmed, warnings = self.detector.detect(
                image,
                self.config(
                    min_radius_px=30,
                    max_radius_px=50,
                    expected_circle_count=3,
                    min_candidate_score=0.0,
                ),
            )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(selected, 0)
        self.assertTrue(any("预期检测到 3" in item for item in warnings))

    def test_no_circle_returns_empty_unconfirmed_result(self):
        image = np.zeros((200, 200), dtype=np.uint8)

        with patch("cv2.HoughCircles", return_value=None):
            candidates, selected, confirmed, warnings = self.detector.detect(
                image,
                self.config(),
            )

        self.assertEqual(candidates, [])
        self.assertIsNone(selected)
        self.assertFalse(confirmed)
        self.assertTrue(warnings)

    def test_low_score_is_selected_but_not_confirmed(self):
        image = np.zeros((200, 200), dtype=np.uint8)
        raw = np.array([[[100.0, 100.0, 40.0]]])

        with patch("cv2.HoughCircles", return_value=raw):
            candidates, selected, confirmed, warnings = self.detector.detect(
                image,
                self.config(
                    min_radius_px=30,
                    max_radius_px=50,
                    min_candidate_score=0.9,
                ),
            )

        self.assertEqual(selected, 0)
        self.assertFalse(confirmed)
        self.assertTrue(any("低于自动确认阈值" in item for item in warnings))

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

    def test_module_does_not_import_gui_or_model_libraries(self):
        module_path = (
            Path(__file__).resolve().parents[1]
            / "backend"
            / "circle_detection.py"
        )
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
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


if __name__ == "__main__":
    unittest.main()
