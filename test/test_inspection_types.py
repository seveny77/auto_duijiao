# -*- coding: utf-8 -*-
"""语义分割质检数据契约测试。"""

import ast
from dataclasses import fields
import json
import subprocess
import sys
import unittest
from pathlib import Path

from backend.inspection_types import (
    CircleCandidate,
    CircleInspectionResult,
    ImageInspectionResult,
    InspectionRecord,
    InspectionRegionRule,
    InspectionResult,
    InspectionStatus,
    RegionInspectionResult,
    RoiRegion,
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


class MultiCircleInspectionTypesTest(unittest.TestCase):
    """只验证多端面数据传递，不执行裁切、推理或汇总判定。"""

    def test_empty_results_are_pending_and_incomplete(self):
        image = ImageInspectionResult()
        circle = CircleInspectionResult()

        self.assertEqual(image.status, InspectionStatus.PENDING)
        self.assertFalse(image.is_complete)
        self.assertEqual(image.expected_circle_count, 0)
        self.assertEqual(image.detected_circle_count, 0)
        self.assertEqual(image.completed_circle_count, 0)
        self.assertEqual(image.circle_results, [])
        self.assertEqual(circle.status, InspectionStatus.PENDING)
        self.assertFalse(circle.completed)
        self.assertFalse(circle.circle_confirmed)
        self.assertIsNone(circle.circle_candidate)
        self.assertIsNone(circle.roi)

    def test_all_new_mutable_defaults_are_independent(self):
        for result_type in (CircleInspectionResult, ImageInspectionResult):
            first, second = result_type(), result_type()
            for item in fields(first):
                value = getattr(first, item.name)
                other = getattr(second, item.name)
                with self.subTest(type=result_type.__name__, field=item.name):
                    if isinstance(value, list):
                        value.append(object())
                        self.assertEqual(other, [])
                    elif isinstance(value, dict):
                        value["inference"] = 12.0
                        self.assertEqual(other, {})

    def test_roi_keeps_requested_and_actual_bounds_separate(self):
        roi = RoiRegion(
            roi_id="roi-1", circle_id="circle-1", source="circle",
            image_width=100, image_height=80,
            requested_bbox=(-10.5, 5.2, 60.4, 90.1),
            x=0, y=5, width=61, height=75, margin_px=10,
        )
        payload = inspection_to_dict(roi)

        self.assertEqual(payload["requested_bbox"], [-10.5, 5.2, 60.4, 90.1])
        self.assertEqual(
            (payload["x"], payload["y"], payload["width"], payload["height"]),
            (0, 5, 61, 75),
        )
        self.assertEqual(payload["image_width"], 100)
        self.assertEqual(payload["image_height"], 80)
        self.assertEqual(payload["circle_id"], "circle-1")

    def test_multiple_circle_results_round_trip_in_original_coordinates(self):
        circles = []
        for index, x in enumerate((100, 500), start=1):
            circle_id = f"circle-{index}"
            circles.append(CircleInspectionResult(
                circle_id=circle_id,
                circle_candidate=CircleCandidate(
                    center_x=x + 100, center_y=200,
                    radius_px=90, score=0.9, source="hough",
                ),
                roi=RoiRegion(
                    roi_id=f"roi-{index}", circle_id=circle_id,
                    source="circle", image_width=1000, image_height=600,
                    requested_bbox=(x, 100, x + 200, 300),
                    x=x, y=100, width=200, height=200, margin_px=10,
                ),
                circle_confirmed=True, completed=True,
                status=InspectionStatus.PASS,
                instances=[SegmentationInstance(
                    class_id=0, class_name="异物", confidence=0.9,
                    polygon=[(x + 10.0, 110.0), (x + 14.0, 110.0),
                             (x + 14.0, 114.0)],
                    bbox=(x + 10.0, 110.0, x + 14.0, 114.0), pixel_area=8,
                )],
                region_results=[RegionInspectionResult(
                    region_id="center", region_name="中心区",
                    class_id=0, class_name="异物", valid_instance_count=1,
                    total_area_mm2=8 * 0.24 ** 2, passed=True,
                )],
                timings_ms={"inference": 12.0},
            ))
        image = ImageInspectionResult(
            image_id="image-1", image_width=1000, image_height=600,
            mm_per_pixel=0.24, expected_circle_count=2,
            detected_circle_count=2, completed_circle_count=2,
            is_complete=True, circle_results=circles,
            status=InspectionStatus.PASS, timings_ms={"total": 30.0},
        )
        payload = json.loads(json.dumps(inspection_to_dict(image), ensure_ascii=False))

        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual(payload["status"], "PASS")
        self.assertTrue(payload["is_complete"])
        self.assertEqual(payload["mm_per_pixel"], 0.24)
        self.assertEqual(len(payload["circle_results"]), 2)
        for index, x in enumerate((100, 500)):
            result = payload["circle_results"][index]
            self.assertEqual(result["roi"]["circle_id"], result["circle_id"])
            self.assertEqual(result["circle_candidate"]["center_x"], x + 100)
            self.assertEqual(result["instances"][0]["polygon"][0], [x + 10, 110])
            self.assertEqual(result["instances"][0]["bbox"], [x + 10, 110, x + 14, 114])
            self.assertEqual(result["instances"][0]["pixel_area"], 8)
            self.assertEqual(result["region_results"][0]["class_name"], "异物")
            self.assertAlmostEqual(result["region_results"][0]["total_area_mm2"], 0.4608)
            self.assertEqual(result["timings_ms"]["inference"], 12.0)

    def test_failure_and_incomplete_processing_are_both_preserved(self):
        image = ImageInspectionResult(
            expected_circle_count=3, detected_circle_count=2,
            completed_circle_count=2, is_complete=False,
            status=InspectionStatus.FAIL,
            warnings=["缺少一个预期端面"],
            failure_reasons=["circle-1: 缺陷数量超限"],
            circle_results=[
                CircleInspectionResult(
                    circle_id="circle-1", completed=True,
                    status=InspectionStatus.FAIL,
                    failure_reasons=["缺陷数量超限"],
                ),
                CircleInspectionResult(
                    circle_id="circle-2", completed=True,
                    status=InspectionStatus.ERROR, error="模型推理失败",
                ),
            ],
        )
        payload = json.loads(json.dumps(inspection_to_dict(image), ensure_ascii=False))

        self.assertEqual(payload["status"], "FAIL")
        self.assertFalse(payload["is_complete"])
        self.assertEqual(payload["completed_circle_count"], 2)
        self.assertEqual(payload["expected_circle_count"], 3)
        self.assertEqual(payload["detected_circle_count"], 2)
        self.assertEqual(payload["warnings"], ["缺少一个预期端面"])
        self.assertEqual(payload["circle_results"][0]["failure_reasons"], ["缺陷数量超限"])
        self.assertEqual(payload["circle_results"][1]["status"], "ERROR")
        self.assertEqual(payload["circle_results"][1]["error"], "模型推理失败")

    def test_legacy_result_and_record_contract_are_unchanged(self):
        result = InspectionResult()
        record = InspectionRecord(result=result)

        self.assertIs(record.result, result)
        self.assertEqual(set(inspection_to_dict(result)), {
            "status", "error", "warnings", "image_width", "image_height",
            "mm_per_pixel", "circle_candidates", "selected_circle_index",
            "circle_confirmed", "instances", "region_results",
            "failure_reasons", "timings_ms",
        })
        self.assertIsInstance(InspectionRecord().result, InspectionResult)

    def test_fresh_import_does_not_load_gui_or_model_dependencies(self):
        project_root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, "-B", "-c",
             "from backend.inspection_types import RoiRegion, CircleInspectionResult, "
             "ImageInspectionResult; import sys, json; "
             "print(json.dumps([name for name in "
             "('PyQt5', 'torch', 'ultralytics', 'cv2', 'numpy') "
             "if name in sys.modules]))"],
            cwd=str(project_root), capture_output=True, text=True, check=True,
        )

        self.assertEqual(json.loads(result.stdout), [])


if __name__ == "__main__":
    unittest.main()
