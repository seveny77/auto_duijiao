# -*- coding: utf-8 -*-
"""新版区域 × 类别 × 尺寸段规则引擎验证。"""

import math
import unittest

from backend.inspection_config import default_inspection_size_rules
from backend.inspection_types import CircleCandidate, InspectionStatus, SegmentationInstance
from backend.size_rule_inspection_engine import (
    SizeRuleInspectionEngine,
    size_rule_matches,
)


def _spot(center_x: float, center_y: float, radius_px: float) -> SegmentationInstance:
    points = []
    for index in range(96):
        angle = 2.0 * math.pi * index / 96.0
        points.append((
            center_x + radius_px * math.cos(angle),
            center_y + radius_px * math.sin(angle),
        ))
    return SegmentationInstance(class_name="污点", polygon=points)


def _scratch(center_x: float, center_y: float) -> SegmentationInstance:
    # 20 px × 4 px 的斜向矩形：宽度 4 µm（scale=1）应触发 B 区划痕规则。
    return SegmentationInstance(
        class_name="划痕",
        polygon=[
            (center_x - 7.66, center_y - 6.73),
            (center_x + 9.66, center_y + 3.27),
            (center_x + 7.66, center_y + 6.73),
            (center_x - 9.66, center_y - 3.27),
        ],
    )


class SizeRuleInspectionEngineTest(unittest.TestCase):
    def setUp(self):
        self.engine = SizeRuleInspectionEngine()
        self.rules = default_inspection_size_rules()

    def _evaluate(self, instances):
        return self.engine.evaluate(
            instances=instances,
            circle_candidates=[CircleCandidate(center_x=0.0, center_y=0.0)],
            selected_circle_index=0,
            circle_confirmed=True,
            um_per_pixel=1.0,
            size_rules=self.rules,
        )

    def test_a_region_any_spot_fails(self):
        result = self._evaluate([_spot(10.0, 0.0, 2.0)])

        self.assertEqual(result.status, InspectionStatus.FAIL)
        self.assertTrue(any(item.rule_id == "A-spot-any" and not item.passed
                            for item in result.size_rule_results))

    def test_b_region_small_spots_are_unlimited_but_medium_are_limited(self):
        small_result = self._evaluate([_spot(50.0, 0.0, 2.0) for _ in range(8)])
        medium_result = self._evaluate([_spot(50.0, 0.0, 3.0) for _ in range(4)])

        self.assertEqual(small_result.status, InspectionStatus.PASS)
        self.assertEqual(medium_result.status, InspectionStatus.FAIL)
        medium = next(item for item in medium_result.size_rule_results
                      if item.rule_id == "B-spot-medium")
        self.assertEqual(medium.actual_instance_count, 4)

    def test_b_region_large_spot_and_wide_scratch_fail(self):
        large_spot = self._evaluate([_spot(50.0, 0.0, 6.0)])
        wide_scratch = self._evaluate([_scratch(50.0, 0.0)])

        self.assertEqual(large_spot.status, InspectionStatus.FAIL)
        self.assertEqual(wide_scratch.status, InspectionStatus.FAIL)

    def test_c_region_is_recorded_but_not_controlled(self):
        result = self._evaluate([_spot(125.0, 0.0, 30.0)])
        c_result = next(item for item in result.size_rule_results
                        if item.rule_id == "C-spot-any")

        self.assertEqual(result.status, InspectionStatus.PASS)
        self.assertEqual(c_result.actual_instance_count, 1)
        self.assertTrue(c_result.passed)

    def test_d_region_medium_limit_and_exact_boundaries(self):
        result = self._evaluate([_spot(150.0, 0.0, 15.0) for _ in range(4)])
        rules = {rule.rule_id: rule for rule in self.rules}

        self.assertEqual(result.status, InspectionStatus.FAIL)
        self.assertFalse(size_rule_matches(rules["B-spot-small"], 5.0))
        self.assertTrue(size_rule_matches(rules["B-spot-medium"], 5.0))
        self.assertTrue(size_rule_matches(rules["B-spot-medium"], 10.0))
        self.assertFalse(size_rule_matches(rules["B-spot-large"], 10.0))
        self.assertTrue(size_rule_matches(rules["D-spot-medium"], 20.0))
        self.assertTrue(size_rule_matches(rules["D-spot-medium"], 50.0))
        self.assertFalse(size_rule_matches(rules["D-spot-large"], 50.0))

    def test_unknown_model_class_is_pending_instead_of_pass(self):
        result = self._evaluate([
            SegmentationInstance(
                class_name="异物",
                polygon=[(48.0, 0.0), (52.0, 0.0), (50.0, 4.0)],
            )
        ])

        self.assertEqual(result.status, InspectionStatus.PENDING)
        self.assertTrue(result.warnings)


if __name__ == "__main__":
    unittest.main()
