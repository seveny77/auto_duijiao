# -*- coding: utf-8 -*-
"""语义分割质检配置及持久化测试。"""

import json
import tempfile
import unittest
from pathlib import Path

from backend.inspection_config import (
    CircleDetectionConfig,
    InspectionConfig,
    InspectionConfigStore,
    inspection_config_from_dict,
)
from backend.inspection_types import InspectionRegionRule


def _valid_rules():
    return [
        InspectionRegionRule(
            region_id="center",
            region_name="中心区",
            inner_radius_mm=0.0,
            outer_radius_mm=5.0,
            class_id=0,
            class_name="scratch",
            min_confidence=0.5,
            min_instance_area_mm2=0.01,
            max_instance_count=1,
        ),
        InspectionRegionRule(
            region_id="middle",
            region_name="中间环",
            inner_radius_mm=5.0,
            outer_radius_mm=12.0,
            class_id=0,
            class_name="scratch",
            min_confidence=0.4,
            min_instance_area_mm2=0.02,
            max_instance_count=3,
        ),
    ]


class InspectionConfigTest(unittest.TestCase):
    def test_default_config_has_independent_mutable_values(self):
        first = InspectionConfig()
        second = InspectionConfig()

        first.region_rules.append(_valid_rules()[0])
        first.circle.min_radius_px = 999

        self.assertEqual(second.region_rules, [])
        self.assertNotEqual(first.circle.min_radius_px, second.circle.min_radius_px)

    def test_valid_config_has_no_errors(self):
        config = InspectionConfig(
            model_path="assets/models/seg/best.pt",
            mm_per_pixel=0.01,
            region_rules=_valid_rules(),
        )

        self.assertEqual(config.validate(), [])

    def test_invalid_physical_scale_is_reported(self):
        config = InspectionConfig(
            mm_per_pixel=0.0,
            region_rules=_valid_rules(),
        )

        self.assertTrue(any("mm_per_pixel" in item for item in config.validate()))

    def test_invalid_segmentation_inference_values_are_reported(self):
        config = InspectionConfig(
            mm_per_pixel=0.01,
            region_rules=_valid_rules(),
            inference_imgsz=0,
            inference_confidence_floor=1.1,
        )

        errors = config.validate()
        self.assertTrue(any("inference_imgsz" in item for item in errors))
        self.assertTrue(any("置信度下限" in item for item in errors))

    def test_invalid_hough_values_and_circle_count_are_reported(self):
        config = InspectionConfig(
            mm_per_pixel=0.01,
            region_rules=_valid_rules(),
            circle=CircleDetectionConfig(
                hough_param1=0.0,
                hough_param2=-1.0,
                expected_circle_count=0,
            ),
        )

        errors = config.validate()
        self.assertTrue(any("param1" in item for item in errors))
        self.assertTrue(any("param2" in item for item in errors))
        self.assertTrue(any("预期圆数量" in item for item in errors))

    def test_evaluation_validation_does_not_depend_on_hough_values(self):
        config = InspectionConfig(
            mm_per_pixel=0.01,
            region_rules=_valid_rules(),
        )
        config.circle.min_radius_px = 100
        config.circle.max_radius_px = 50

        self.assertTrue(config.validate())
        self.assertEqual(config.validate_evaluation(), [])

    def test_invalid_ring_gap_and_duplicate_rule_are_reported(self):
        rules = _valid_rules()
        rules[1].inner_radius_mm = 6.0
        rules.append(rules[0])
        config = InspectionConfig(mm_per_pixel=0.01, region_rules=rules)
        errors = config.validate()

        self.assertTrue(any("空隙或重叠" in item for item in errors))
        self.assertTrue(any("规则重复" in item for item in errors))

    def test_save_and_load_round_trip(self):
        config = InspectionConfig(
            enabled=True,
            model_path="assets/models/seg/best.pt",
            mm_per_pixel=0.0125,
            history_root="inspection_history",
            circle=CircleDetectionConfig(min_candidate_score=0.9),
            region_rules=_valid_rules(),
            roi_size_px=1536,
        )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "inspection_config.json"
            store = InspectionConfigStore(str(path))
            store.save(config)
            restored = store.load()

            self.assertEqual(restored, config)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["region_rules"][0]["region_name"], "中心区")
            self.assertEqual(payload["roi_size_px"], 1536)
            self.assertFalse(Path(f"{path}.tmp").exists())

    def test_missing_file_returns_default_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing.json"
            restored = InspectionConfigStore(str(path)).load()

        self.assertEqual(restored, InspectionConfig())
        self.assertEqual(restored.roi_size_px, 1024)

    def test_old_config_missing_fields_uses_defaults(self):
        restored = inspection_config_from_dict({
            "enabled": False,
            "mm_per_pixel": 0.02,
        })

        self.assertFalse(restored.enabled)
        self.assertEqual(restored.mm_per_pixel, 0.02)
        self.assertEqual(restored.circle, CircleDetectionConfig())
        self.assertEqual(restored.region_rules, [])
        self.assertEqual(restored.history_root, "inspection_history")
        self.assertEqual(restored.inference_imgsz, 4096)
        self.assertEqual(restored.inference_confidence_floor, 0.01)
        self.assertEqual(restored.roi_size_px, 1024)

    def test_roi_size_is_independent_of_circle_radius_and_inference_size(self):
        config = inspection_config_from_dict({
            "mm_per_pixel": 0.24,
            "roi_size_px": 701,
            "inference_imgsz": 1920,
            "circle": {"min_radius_px": 100, "max_radius_px": 2000},
        })

        self.assertEqual(config.validate(), [])
        self.assertEqual(config.roi_size_px, 701)
        self.assertEqual(config.inference_imgsz, 1920)
        config.circle.max_radius_px = 3000
        config.inference_imgsz = 4096
        self.assertEqual(config.roi_size_px, 701)

    def test_invalid_roi_sizes_are_rejected_without_silent_coercion(self):
        for value in (0, -1, 1024.5, 1024.0, True, False, "1024", None,
                      [], {}, float("nan"), float("inf")):
            with self.subTest(value=value):
                config = InspectionConfig(mm_per_pixel=0.24, roi_size_px=value)
                self.assertTrue(any("roi_size_px" in error for error in config.validate()))
                with self.assertRaisesRegex(ValueError, "roi_size_px"):
                    inspection_config_from_dict({"roi_size_px": value})

    def test_invalid_roi_save_preserves_existing_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inspection_config.json"
            store = InspectionConfigStore(str(path))
            store.save(InspectionConfig(mm_per_pixel=0.24, roi_size_px=800))
            original = path.read_bytes()

            for value in (0, 12.5, True):
                with self.subTest(value=value):
                    with self.assertRaisesRegex(ValueError, "roi_size_px"):
                        store.save(InspectionConfig(roi_size_px=value))
                    self.assertEqual(path.read_bytes(), original)
                    self.assertFalse(Path(f"{path}.tmp").exists())

    def test_roi_validation_does_not_block_lightweight_rule_evaluation(self):
        config = InspectionConfig(
            mm_per_pixel=0.24, region_rules=_valid_rules(), roi_size_px=0,
        )

        self.assertTrue(any("roi_size_px" in error for error in config.validate()))
        # 复判只使用已有实例；裁切宽度不属于其输入，也不会触发新推理。
        self.assertEqual(config.validate_evaluation(), [])

    def test_old_json_load_does_not_rewrite_runtime_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inspection_config.json"
            old_payload = {
                "model_path": "models/best.pt",
                "inference_imgsz": 4096,
                "inference_confidence_floor": 0.1,
                "mm_per_pixel": 0.24,
            }
            path.write_text(json.dumps(old_payload), encoding="utf-8")
            original = path.read_bytes()
            restored = InspectionConfigStore(str(path)).load()

            self.assertEqual(restored.roi_size_px, 1024)
            self.assertEqual(restored.model_path, "models/best.pt")
            self.assertEqual(restored.inference_imgsz, 4096)
            self.assertEqual(restored.inference_confidence_floor, 0.1)
            self.assertEqual(restored.mm_per_pixel, 0.24)
            self.assertEqual(path.read_bytes(), original)

    def test_old_total_area_limit_is_ignored(self):
        restored = inspection_config_from_dict({
            "region_rules": [
                {
                    "region_id": "center",
                    "region_name": "中心区",
                    "inner_radius_mm": 0.0,
                    "outer_radius_mm": 5.0,
                    "class_id": 0,
                    "class_name": "scratch",
                    "min_confidence": 0.5,
                    "min_instance_area_mm2": 0.01,
                    "max_total_area_mm2": 99.0,
                    "max_instance_count": 1,
                }
            ]
        })

        self.assertFalse(hasattr(
            restored.region_rules[0],
            "max_total_area_mm2",
        ))

    def test_old_score_gap_is_ignored(self):
        restored = inspection_config_from_dict({
            "circle": {
                "min_score_gap": 0.99,
                "expected_circle_count": 2,
            }
        })

        self.assertFalse(hasattr(restored.circle, "min_score_gap"))
        self.assertEqual(restored.circle.expected_circle_count, 2)

    def test_invalid_json_shapes_raise_clear_errors(self):
        with self.assertRaisesRegex(ValueError, "circle"):
            inspection_config_from_dict({"circle": []})

        with self.assertRaisesRegex(ValueError, "region_rules"):
            inspection_config_from_dict({"region_rules": {}})


if __name__ == "__main__":
    unittest.main()
