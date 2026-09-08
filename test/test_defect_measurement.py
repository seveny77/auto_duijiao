# -*- coding: utf-8 -*-
"""污点等效直径、划痕旋转宽度的纯后处理测试。"""

import math
import unittest

from backend.defect_measurement import measure_instance, measure_instances
from backend.inspection_types import SegmentationInstance


class DefectMeasurementTest(unittest.TestCase):
    def test_square_polygon_measures_area_and_equivalent_diameter(self):
        instance = SegmentationInstance(
            polygon=[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)],
            bbox=(0.0, 0.0, 10.0, 10.0),
        )
        measured = measure_instance(instance, um_per_pixel=0.5)

        self.assertEqual(measured.source, "polygon")
        self.assertFalse(measured.estimated)
        self.assertAlmostEqual(measured.area_px2, 100.0)
        self.assertAlmostEqual(measured.area_um2, 25.0)
        self.assertAlmostEqual(
            measured.equivalent_diameter_um,
            2.0 * math.sqrt(25.0 / math.pi),
        )
        self.assertAlmostEqual(measured.rotated_width_um, 5.0)
        self.assertAlmostEqual(measured.rotated_length_um, 5.0)

    def test_rotated_scratch_uses_short_side_as_width(self):
        # 长 40 px、宽 4 px 的 30° 旋转矩形。
        instance = SegmentationInstance(
            polygon=[
                (-16.3205, -11.7321), (18.3205, 8.2679),
                (16.3205, 11.7321), (-18.3205, -8.2679),
            ],
            bbox=(-18.4, -11.8, 18.4, 11.8),
        )
        measured = measure_instance(instance, um_per_pixel=0.25)

        self.assertEqual(measured.source, "polygon")
        self.assertAlmostEqual(measured.rotated_length_um, 10.0, places=3)
        self.assertAlmostEqual(measured.rotated_width_um, 1.0, places=3)

    def test_invalid_polygon_falls_back_to_bbox_estimate(self):
        instance = SegmentationInstance(
            polygon=[],
            bbox=(10.0, 20.0, 30.0, 26.0),
            pixel_area=100,
        )
        measured = measure_instance(instance, um_per_pixel=0.2)

        self.assertEqual(measured.source, "bbox_estimate")
        self.assertTrue(measured.estimated)
        self.assertAlmostEqual(measured.area_um2, 4.0)
        self.assertAlmostEqual(measured.rotated_width_um, 1.2)
        self.assertTrue(measured.warnings)

    def test_invalid_scale_does_not_create_physical_measurement(self):
        measured = measure_instance(
            SegmentationInstance(
                polygon=[(0.0, 0.0), (2.0, 0.0), (0.0, 2.0)],
            ),
            um_per_pixel=0.0,
        )

        self.assertEqual(measured.source, "invalid")
        self.assertIsNone(measured.area_um2)
        self.assertIsNone(measured.equivalent_diameter_um)
        self.assertTrue(measured.warnings)

    def test_batch_measurement_preserves_input_order_and_index(self):
        instances = [
            SegmentationInstance(polygon=[(0, 0), (1, 0), (0, 1)]),
            SegmentationInstance(polygon=[(0, 0), (2, 0), (0, 2)]),
        ]
        result = measure_instances(instances, um_per_pixel=1.0)

        self.assertEqual([item.instance_index for item in result], [0, 1])


if __name__ == "__main__":
    unittest.main()
