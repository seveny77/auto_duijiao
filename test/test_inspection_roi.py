# -*- coding: utf-8 -*-
"""固定正方形 ROI、子图裁切与坐标恢复测试。"""

import ast
import unittest
from pathlib import Path

import numpy as np

from backend.inspection_roi import (
    build_circle_roi,
    build_circle_rois,
    crop_roi,
    is_roi_clipped,
    restore_instance_to_image,
    restore_instances_to_image,
)
from backend.inspection_types import CircleCandidate, RoiRegion, SegmentationInstance


class InspectionRoiTest(unittest.TestCase):
    def test_center_circle_builds_requested_square(self):
        circle = CircleCandidate(center_x=500.0, center_y=300.0, radius_px=5.0)

        roi = build_circle_roi(
            circle,
            circle_id="circle-001",
            roi_size_px=200,
            image_width=1000,
            image_height=600,
        )

        self.assertEqual(roi.roi_id, "roi-circle-001")
        self.assertEqual(roi.circle_id, "circle-001")
        self.assertEqual(roi.source, "circle")
        self.assertEqual(roi.requested_bbox, (400.0, 200.0, 600.0, 400.0))
        self.assertEqual((roi.x, roi.y, roi.width, roi.height), (400, 200, 200, 200))
        self.assertEqual(roi.margin_px, 0)
        self.assertFalse(is_roi_clipped(roi))

    def test_odd_size_and_fractional_center_follow_floor_rule(self):
        roi = build_circle_roi(
            CircleCandidate(center_x=10.75, center_y=20.25),
            circle_id="c",
            roi_size_px=5,
            image_width=40,
            image_height=40,
        )

        self.assertEqual(roi.requested_bbox, (8.0, 17.0, 13.0, 22.0))
        self.assertEqual((roi.x, roi.y, roi.width, roi.height), (8, 17, 5, 5))

    def test_circle_radius_and_score_do_not_change_roi(self):
        common = {
            "circle_id": "c",
            "roi_size_px": 101,
            "image_width": 500,
            "image_height": 500,
        }
        first = build_circle_roi(
            CircleCandidate(center_x=250, center_y=250, radius_px=5, score=0.1),
            **common,
        )
        second = build_circle_roi(
            CircleCandidate(center_x=250, center_y=250, radius_px=200, score=0.99),
            **common,
        )

        self.assertEqual(first, second)

    def test_multiple_circles_keep_input_order_and_allow_overlap(self):
        circles = [
            CircleCandidate(center_x=100, center_y=100),
            CircleCandidate(center_x=140, center_y=100),
            CircleCandidate(center_x=300, center_y=300),
        ]

        rois = build_circle_rois(
            circles,
            roi_size_px=100,
            image_width=400,
            image_height=400,
        )

        self.assertEqual(
            [roi.circle_id for roi in rois],
            ["circle-001", "circle-002", "circle-003"],
        )
        self.assertEqual([roi.x for roi in rois], [50, 90, 250])
        self.assertGreater(rois[0].x + rois[0].width, rois[1].x)

    def test_edge_roi_is_clipped_and_keeps_original_request(self):
        roi = build_circle_roi(
            CircleCandidate(center_x=20, center_y=10),
            circle_id="edge",
            roi_size_px=100,
            image_width=200,
            image_height=150,
        )

        self.assertEqual(roi.requested_bbox, (-30.0, -40.0, 70.0, 60.0))
        self.assertEqual((roi.x, roi.y, roi.width, roi.height), (0, 0, 70, 60))
        self.assertTrue(is_roi_clipped(roi))

    def test_invalid_circle_and_dimensions_raise_clear_errors(self):
        valid = CircleCandidate(center_x=5, center_y=5)
        calls = [
            ({"circle": None}, TypeError, "CircleCandidate"),
            ({"circle": CircleCandidate(center_x=float("nan"), center_y=5)},
             ValueError, "有限"),
            ({"circle": CircleCandidate(center_x=10, center_y=5)},
             ValueError, "原图范围"),
            ({"circle_id": ""}, ValueError, "circle_id"),
            ({"roi_size_px": 0}, ValueError, "roi_size_px"),
            ({"roi_size_px": 10.5}, ValueError, "roi_size_px"),
            ({"image_width": 0}, ValueError, "image_width"),
            ({"image_height": True}, ValueError, "image_height"),
        ]
        defaults = {
            "circle": valid,
            "circle_id": "c",
            "roi_size_px": 4,
            "image_width": 10,
            "image_height": 10,
        }
        for changes, error_type, pattern in calls:
            arguments = dict(defaults)
            arguments.update(changes)
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(error_type, pattern):
                    build_circle_roi(**arguments)

    def test_invalid_item_in_multiple_circles_identifies_index(self):
        with self.assertRaisesRegex(TypeError, r"候选圆\[1\]"):
            build_circle_rois(
                [CircleCandidate(center_x=5, center_y=5), None],
                roi_size_px=4,
                image_width=10,
                image_height=10,
            )

    def test_crop_returns_independent_grayscale_and_color_patches(self):
        roi = build_circle_roi(
            CircleCandidate(center_x=3, center_y=3),
            circle_id="c",
            roi_size_px=4,
            image_width=6,
            image_height=6,
        )
        grayscale = np.arange(36, dtype=np.uint8).reshape(6, 6)
        color = np.repeat(grayscale[:, :, None], 3, axis=2)

        gray_patch = crop_roi(grayscale, roi)
        color_patch = crop_roi(color, roi)

        self.assertTrue(np.array_equal(gray_patch, grayscale[1:5, 1:5]))
        self.assertTrue(np.array_equal(color_patch, color[1:5, 1:5]))
        gray_patch[0, 0] = 255
        color_patch[0, 0, :] = 255
        self.assertEqual(grayscale[1, 1], 7)
        self.assertTrue(np.array_equal(color[1, 1], (7, 7, 7)))

    def test_crop_checks_image_and_roi_consistency(self):
        roi = RoiRegion(
            roi_id="r", circle_id="c", source="circle",
            image_width=10, image_height=10,
            requested_bbox=(2, 2, 8, 8), x=2, y=2, width=6, height=6,
        )
        with self.assertRaisesRegex(ValueError, "尺寸与当前图像不一致"):
            crop_roi(np.zeros((9, 10), dtype=np.uint8), roi)
        with self.assertRaisesRegex(ValueError, "有效"):
            crop_roi(None, roi)

        invalid = RoiRegion(
            image_width=10, image_height=10,
            requested_bbox=(8, 8, 12, 12), x=8, y=8, width=4, height=4,
        )
        with self.assertRaisesRegex(ValueError, "超出原图"):
            crop_roi(np.zeros((10, 10), dtype=np.uint8), invalid)

    def test_restore_instance_offsets_polygon_and_bbox_without_mutation(self):
        roi = RoiRegion(
            roi_id="r", circle_id="c", source="circle",
            image_width=1000, image_height=800,
            requested_bbox=(100, 200, 300, 400),
            x=100, y=200, width=200, height=200,
        )
        source = SegmentationInstance(
            class_id=1, class_name="脏污", confidence=0.81,
            polygon=[(1.5, 2.5), (4.0, 2.5), (4.0, 6.0)],
            bbox=(1.5, 2.5, 4.0, 6.0), pixel_area=9,
        )

        restored = restore_instance_to_image(source, roi)

        self.assertIsNot(restored, source)
        self.assertEqual(restored.polygon, [(101.5, 202.5), (104.0, 202.5), (104.0, 206.0)])
        self.assertEqual(restored.bbox, (101.5, 202.5, 104.0, 206.0))
        self.assertEqual(restored.class_id, source.class_id)
        self.assertEqual(restored.class_name, source.class_name)
        self.assertEqual(restored.confidence, source.confidence)
        self.assertEqual(restored.pixel_area, source.pixel_area)
        self.assertEqual(source.polygon[0], (1.5, 2.5))
        self.assertEqual(source.bbox, (1.5, 2.5, 4.0, 6.0))

    def test_batch_restore_returns_new_instances(self):
        roi = RoiRegion(
            image_width=20, image_height=20,
            requested_bbox=(5, 5, 15, 15), x=5, y=5, width=10, height=10,
        )
        source = [SegmentationInstance(
            polygon=[(0, 0), (1, 0), (1, 1)], bbox=(0, 0, 1, 1),
        )]

        restored = restore_instances_to_image(source, roi)

        self.assertIsNot(restored, source)
        self.assertIsNot(restored[0], source[0])
        self.assertEqual(restored[0].polygon[0], (5.0, 5.0))

    def test_module_has_no_gui_model_or_opencv_imports(self):
        module_path = (
            Path(__file__).resolve().parents[1]
            / "backend"
            / "inspection_roi.py"
        )
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        imported_roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(
                    alias.name.split(".", 1)[0] for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".", 1)[0])

        forbidden = {"PyQt5", "torch", "ultralytics", "cv2", "numpy"}
        self.assertTrue(forbidden.isdisjoint(imported_roots))


if __name__ == "__main__":
    unittest.main()
