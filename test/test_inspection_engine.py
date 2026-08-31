# -*- coding: utf-8 -*-
"""基于缺陷质心的最简质检规则引擎测试。"""

import ast
import unittest
from pathlib import Path

from backend.inspection_engine import InspectionRuleEngine
from backend.inspection_types import (
    CircleCandidate,
    InspectionRegionRule,
    InspectionStatus,
    SegmentationInstance,
)


def _rule(
    region_id,
    region_name,
    inner_radius_mm,
    outer_radius_mm,
    *,
    class_id=0,
    class_name="scratch",
    min_confidence=0.5,
    min_instance_area_mm2=0.01,
    max_instance_count=1,
):
    return InspectionRegionRule(
        region_id=region_id,
        region_name=region_name,
        inner_radius_mm=inner_radius_mm,
        outer_radius_mm=outer_radius_mm,
        class_id=class_id,
        class_name=class_name,
        min_confidence=min_confidence,
        min_instance_area_mm2=min_instance_area_mm2,
        max_instance_count=max_instance_count,
    )


def _instance(center_x, center_y, *, confidence=0.9, pixel_area=200, class_id=0):
    half = 1.0
    return SegmentationInstance(
        class_id=class_id,
        class_name="scratch" if class_id == 0 else "crack",
        confidence=confidence,
        polygon=[
            (center_x - half, center_y - half),
            (center_x + half, center_y - half),
            (center_x + half, center_y + half),
            (center_x - half, center_y + half),
        ],
        bbox=(
            center_x - half,
            center_y - half,
            center_x + half,
            center_y + half,
        ),
        pixel_area=pixel_area,
    )


class InspectionEngineTest(unittest.TestCase):
    def setUp(self):
        self.engine = InspectionRuleEngine()
        self.candidates = [CircleCandidate(center_x=100.0, center_y=100.0)]
        self.rules = [
            _rule("center", "中心区", 0.0, 5.0),
            _rule("middle", "中间环", 5.0, 10.0),
            _rule("outer", "外围环", 10.0, 20.0),
        ]

    def evaluate(self, instances, **overrides):
        values = {
            "instances": instances,
            "circle_candidates": self.candidates,
            "selected_circle_index": 0,
            "circle_confirmed": True,
            "mm_per_pixel": 0.1,
            "region_rules": self.rules,
            "image_width": 200,
            "image_height": 200,
        }
        values.update(overrides)
        return self.engine.evaluate(**values)

    def test_centroids_are_assigned_to_expected_regions(self):
        result = self.evaluate([
            _instance(120.0, 100.0),
            _instance(160.0, 100.0),
            _instance(220.0, 100.0),
        ])

        self.assertEqual(
            [item.valid_instance_count for item in result.region_results],
            [1, 1, 1],
        )

    def test_inner_boundary_belongs_to_following_region(self):
        result = self.evaluate([_instance(150.0, 100.0)])

        self.assertEqual(result.region_results[0].valid_instance_count, 0)
        self.assertEqual(result.region_results[1].valid_instance_count, 1)

    def test_cross_ring_instance_is_assigned_by_centroid(self):
        instance = _instance(160.0, 100.0)
        instance.polygon = [
            (140.0, 99.0),
            (180.0, 99.0),
            (180.0, 101.0),
            (140.0, 101.0),
        ]

        result = self.evaluate([instance])

        self.assertEqual(result.region_results[1].valid_instance_count, 1)

    def test_confidence_and_minimum_area_filter_instances(self):
        result = self.evaluate([
            _instance(120.0, 100.0, confidence=0.4, pixel_area=200),
            _instance(120.0, 100.0, confidence=0.9, pixel_area=0),
            _instance(120.0, 100.0, confidence=0.9, pixel_area=200),
        ])

        center = result.region_results[0]
        self.assertEqual(center.valid_instance_count, 1)
        self.assertAlmostEqual(center.total_area_mm2, 2.0)

    def test_count_equal_to_limit_passes_and_above_limit_fails(self):
        passed = self.evaluate([_instance(120.0, 100.0)])
        failed = self.evaluate([
            _instance(120.0, 100.0),
            _instance(130.0, 100.0),
        ])

        self.assertEqual(passed.status, InspectionStatus.PASS)
        self.assertEqual(failed.status, InspectionStatus.FAIL)
        self.assertFalse(failed.region_results[0].passed)
        self.assertTrue(failed.failure_reasons)

    def test_pending_prerequisites_do_not_produce_pass(self):
        unconfirmed = self.evaluate([], circle_confirmed=False)
        invalid_scale = self.evaluate([], mm_per_pixel=0.0)

        self.assertEqual(unconfirmed.status, InspectionStatus.PENDING)
        self.assertEqual(invalid_scale.status, InspectionStatus.PENDING)

    def test_missing_region_class_rule_is_pending(self):
        result = self.evaluate([
            _instance(120.0, 100.0, class_id=1),
        ])

        self.assertEqual(result.status, InspectionStatus.PENDING)
        self.assertTrue(any("缺少类别" in item for item in result.warnings))

    def test_invalid_polygon_falls_back_to_bbox_center(self):
        instance = _instance(160.0, 100.0)
        instance.polygon = [(0.0, 0.0), (1.0, 1.0)]

        result = self.evaluate([instance])

        self.assertEqual(result.region_results[1].valid_instance_count, 1)

    def test_total_area_is_informational_and_does_not_fail(self):
        rules = [
            _rule(
                "center",
                "中心区",
                0.0,
                5.0,
                max_instance_count=2,
            )
        ]
        result = self.evaluate(
            [
                _instance(120.0, 100.0, pixel_area=100000),
                _instance(130.0, 100.0, pixel_area=100000),
            ],
            region_rules=rules,
        )

        self.assertEqual(result.status, InspectionStatus.PASS)
        self.assertAlmostEqual(result.region_results[0].total_area_mm2, 2000.0)

    def test_reevaluate_reuses_inference_and_circle_outputs(self):
        instances = [
            _instance(120.0, 100.0),
            _instance(130.0, 100.0),
        ]
        source = self.evaluate(instances)
        relaxed_rules = [
            _rule("center", "中心区", 0.0, 5.0, max_instance_count=2),
            _rule("middle", "中间环", 5.0, 10.0, max_instance_count=2),
            _rule("outer", "外围环", 10.0, 20.0, max_instance_count=2),
        ]

        recalculated = self.engine.reevaluate(
            source,
            mm_per_pixel=0.1,
            region_rules=relaxed_rules,
        )

        self.assertEqual(source.status, InspectionStatus.FAIL)
        self.assertEqual(recalculated.status, InspectionStatus.PASS)
        self.assertIs(recalculated.instances[0], source.instances[0])
        self.assertIs(
            recalculated.circle_candidates[0],
            source.circle_candidates[0],
        )
        self.assertEqual(recalculated.image_width, source.image_width)
        self.assertEqual(recalculated.image_height, source.image_height)

    def test_module_has_no_heavy_or_gui_imports(self):
        module_path = (
            Path(__file__).resolve().parents[1]
            / "backend"
            / "inspection_engine.py"
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

        forbidden = {"PyQt5", "torch", "ultralytics", "cv2", "numpy"}
        self.assertTrue(forbidden.isdisjoint(imported_roots))


if __name__ == "__main__":
    unittest.main()
