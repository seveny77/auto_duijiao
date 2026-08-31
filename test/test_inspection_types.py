# -*- coding: utf-8 -*-
"""语义分割质检数据契约测试。"""

import ast
import json
import unittest
from pathlib import Path

from backend.inspection_types import (
    CircleCandidate,
    InspectionRecord,
    InspectionRegionRule,
    InspectionResult,
    InspectionStatus,
    RegionInspectionResult,
    SegmentationInstance,
    inspection_to_dict,
)


class InspectionTypesTest(unittest.TestCase):
    """验证数据契约的默认值隔离和 JSON 兼容性。"""

    def test_status_values(self):
        self.assertEqual(InspectionStatus.PASS.value, "PASS")
        self.assertEqual(InspectionStatus.FAIL.value, "FAIL")
        self.assertEqual(InspectionStatus.PENDING.value, "PENDING")
        self.assertEqual(InspectionStatus.ERROR.value, "ERROR")

    def test_region_rule_has_no_total_area_limit(self):
        rule = InspectionRegionRule()

        self.assertFalse(hasattr(rule, "max_total_area_mm2"))

    def test_instance_polygon_default_is_not_shared(self):
        first = SegmentationInstance()
        second = SegmentationInstance()

        first.polygon.append((10.0, 20.0))

        self.assertEqual(first.polygon, [(10.0, 20.0)])
        self.assertEqual(second.polygon, [])

    def test_result_mutable_defaults_are_not_shared(self):
        first = InspectionResult()
        second = InspectionResult()

        first.circle_candidates.append(CircleCandidate(score=0.9))
        first.instances.append(SegmentationInstance(class_name="scratch"))
        first.timings_ms["inference"] = 12.5

        self.assertEqual(second.circle_candidates, [])
        self.assertEqual(second.instances, [])
        self.assertEqual(second.timings_ms, {})

    def test_complete_result_is_json_serializable(self):
        instance = SegmentationInstance(
            class_id=2,
            class_name="scratch",
            confidence=0.93,
            polygon=[(10.5, 20.5), (30.0, 40.0), (12.0, 45.0)],
            bbox=(10.5, 20.5, 30.0, 45.0),
            pixel_area=321,
        )
        candidate = CircleCandidate(
            center_x=1000.0,
            center_y=900.0,
            radius_px=800.0,
            score=0.95,
            source="merged",
        )
        rule = InspectionRegionRule(
            region_id="center",
            region_name="中心区",
            inner_radius_mm=0.0,
            outer_radius_mm=5.0,
            class_id=2,
            class_name="scratch",
            min_confidence=0.5,
            min_instance_area_mm2=0.01,
            max_instance_count=2,
        )
        region_result = RegionInspectionResult(
            region_id=rule.region_id,
            region_name=rule.region_name,
            class_id=rule.class_id,
            class_name=rule.class_name,
            valid_instance_count=1,
            total_area_mm2=0.0321,
            passed=True,
        )
        result = InspectionResult(
            status=InspectionStatus.PASS,
            image_width=5472,
            image_height=3648,
            mm_per_pixel=0.01,
            circle_candidates=[candidate],
            selected_circle_index=0,
            circle_confirmed=True,
            instances=[instance],
            region_results=[region_result],
            timings_ms={"inference": 42.0, "evaluation": 3.5},
        )
        record = InspectionRecord(
            record_id="inspection-001",
            detected_at="2026-08-27T12:00:00+08:00",
            model_name="best-seg.pt",
            model_path="assets/models/seg/best-seg.pt",
            original_image_path="original.jpg",
            overlay_image_path="overlay.jpg",
            thumbnail_image_path="thumbnail.jpg",
            result_json_path="result.json",
            result=result,
        )

        payload = inspection_to_dict(record)
        encoded = json.dumps(payload, ensure_ascii=False)

        self.assertIn("中心区", encoded)
        self.assertEqual(payload["result"]["status"], "PASS")
        self.assertEqual(
            payload["result"]["instances"][0]["polygon"][0],
            [10.5, 20.5],
        )
        self.assertEqual(
            payload["result"]["instances"][0]["bbox"],
            [10.5, 20.5, 30.0, 45.0],
        )

    def test_module_has_no_heavy_or_gui_imports(self):
        module_path = (
            Path(__file__).resolve().parents[1]
            / "backend"
            / "inspection_types.py"
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
