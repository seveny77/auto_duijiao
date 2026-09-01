# -*- coding: utf-8 -*-
"""只绘制缺陷轮廓的质检结果图测试。"""

import unittest

import numpy as np

from backend.inspection_config import InspectionConfig
from backend.inspection_renderer import (
    render_image_inspection_overlay,
    render_inspection_overlay,
)
from backend.inspection_types import (
    CircleCandidate,
    CircleInspectionResult,
    ImageInspectionResult,
    InspectionRegionRule,
    InspectionResult,
    RoiRegion,
    SegmentationInstance,
)


class InspectionRendererTest(unittest.TestCase):
    def test_polygon_draws_outline_without_filling_inside(self):
        image = np.full((100, 100, 3), 120, dtype=np.uint8)
        result = InspectionResult(instances=[SegmentationInstance(
            class_id=0,
            polygon=[(20, 20), (80, 20), (80, 80), (20, 80)],
        )])

        rendered = render_inspection_overlay(
            image,
            result,
            InspectionConfig(),
            show_circle=False,
            show_rings=False,
        )

        self.assertTrue(np.array_equal(rendered[50, 50], image[50, 50]))
        self.assertFalse(np.array_equal(rendered[20, 50], image[20, 50]))
        self.assertTrue(np.array_equal(image, np.full_like(image, 120)))

    def test_all_classes_use_red_outline(self):
        image = np.zeros((100, 100, 3), dtype=np.uint8)
        result = InspectionResult(instances=[
            SegmentationInstance(
                class_id=0,
                polygon=[(10, 10), (40, 10), (40, 40), (10, 40)],
            ),
            SegmentationInstance(
                class_id=1,
                polygon=[(60, 60), (90, 60), (90, 90), (60, 90)],
            ),
        ])

        rendered = render_inspection_overlay(
            image,
            result,
            InspectionConfig(),
            show_circle=False,
            show_rings=False,
        )

        # OpenCV 的 LINE_AA 会对轮廓边缘做抗锯齿，因此不要求像素必须是
        # 精确的 (0, 0, 255)，只验证红色通道明显占优。
        for pixel in (rendered[10, 25], rendered[60, 75]):
            self.assertGreater(int(pixel[2]), int(pixel[0]) + 100)
            self.assertGreater(int(pixel[2]), int(pixel[1]) + 100)

    def test_black_background_contains_only_requested_lines(self):
        image = np.full((100, 100, 3), 200, dtype=np.uint8)
        result = InspectionResult(instances=[SegmentationInstance(
            polygon=[(10, 10), (40, 10), (40, 40), (10, 40)],
        )])

        rendered = render_inspection_overlay(
            image,
            result,
            InspectionConfig(),
            background="black",
            show_circle=False,
            show_rings=False,
        )

        self.assertTrue(np.array_equal(rendered[70, 70], [0, 0, 0]))
        self.assertTrue(np.any(rendered[10, 25] != 0))

    def test_selected_circle_and_physical_ring_are_drawn(self):
        image = np.zeros((120, 120, 3), dtype=np.uint8)
        result = InspectionResult(
            circle_candidates=[CircleCandidate(
                center_x=60,
                center_y=60,
                radius_px=40,
            )],
            selected_circle_index=0,
        )
        config = InspectionConfig(
            mm_per_pixel=0.5,
            region_rules=[InspectionRegionRule(
                region_id="r1",
                region_name="中心区",
                outer_radius_mm=10.0,
                class_id=0,
            )],
        )

        rendered = render_inspection_overlay(image, result, config)

        self.assertTrue(np.any(rendered[60, 100] != 0))
        self.assertTrue(np.any(rendered[60, 80] != 0))
        self.assertTrue(np.any(rendered[60, 60] != 0))

    def test_invalid_polygon_and_missing_circle_are_ignored(self):
        image = np.full((30, 30), 50, dtype=np.uint8)
        result = InspectionResult(instances=[SegmentationInstance(
            polygon=[(1, 1), (2, 2)],
        )])

        rendered = render_inspection_overlay(
            image,
            result,
            InspectionConfig(),
        )

        self.assertEqual(rendered.shape, (30, 30, 3))
        self.assertTrue(np.all(rendered == 50))

    def test_multi_circle_overlay_draws_all_rois_circles_and_contours(self):
        image = np.zeros((100, 200, 3), dtype=np.uint8)
        result = ImageInspectionResult(circle_results=[
            CircleInspectionResult(
                circle_id="circle-001",
                circle_candidate=CircleCandidate(
                    center_x=40,
                    center_y=50,
                    radius_px=15,
                ),
                roi=RoiRegion(x=20, y=30, width=40, height=40),
                instances=[SegmentationInstance(
                    class_id=0,
                    polygon=[(25, 35), (55, 35), (55, 65), (25, 65)],
                )],
            ),
            CircleInspectionResult(
                circle_id="circle-002",
                circle_candidate=CircleCandidate(
                    center_x=140,
                    center_y=50,
                    radius_px=15,
                ),
                roi=RoiRegion(x=120, y=30, width=40, height=40),
                instances=[SegmentationInstance(
                    class_id=1,
                    polygon=[
                        (125, 35), (155, 35), (155, 65), (125, 65)
                    ],
                )],
            ),
        ])

        rendered = render_image_inspection_overlay(
            image,
            result,
            InspectionConfig(),
            show_rings=False,
        )

        for pixel in (rendered[35, 40], rendered[35, 140]):
            self.assertGreater(int(pixel[2]), int(pixel[0]) + 100)
            self.assertGreater(int(pixel[2]), int(pixel[1]) + 100)
        self.assertTrue(np.any(rendered[30, 30] != 0))
        self.assertTrue(np.any(rendered[30, 130] != 0))
        self.assertTrue(np.any(rendered[50, 40] != 0))
        self.assertTrue(np.any(rendered[50, 140] != 0))
        self.assertTrue(np.array_equal(rendered[50, 50], image[50, 50]))
        self.assertTrue(np.array_equal(rendered[50, 150], image[50, 150]))
        self.assertTrue(np.array_equal(image, np.zeros_like(image)))

    def test_multi_circle_overlay_ignores_placeholder_results(self):
        image = np.full((40, 40), 60, dtype=np.uint8)
        result = ImageInspectionResult(circle_results=[
            CircleInspectionResult(circle_id="circle-001")
        ])

        rendered = render_image_inspection_overlay(
            image,
            result,
            InspectionConfig(),
        )

        self.assertEqual(rendered.shape, (40, 40, 3))
        self.assertTrue(np.all(rendered == 60))


if __name__ == "__main__":
    unittest.main()
