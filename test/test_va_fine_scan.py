"""精扫触发约定、图像帧选择及独立节点状态传递验证。"""

import json
from pathlib import Path
import runpy
import unittest

from va_scripts.fine_scan import initialize_fine_scan, append_fine_sharpness, finalize_fine_scan


class FineScanTests(unittest.TestCase):
    def test_user_center_plan_includes_center_and_both_sides(self):
        data = json.loads(initialize_fine_scan(11105, 5, 10))
        self.assertEqual(data["start_position"], 11050)
        self.assertEqual(data["first_sample_position"], 11055)
        self.assertEqual(data["end_position"], 11155)
        self.assertEqual(data["expected_count"], 21)
        self.assertEqual(data["center_frame_index"], 10)
        self.assertEqual((data["end_position"] - data["start_position"]) / data["step"], 21)
        self.assertEqual(data["samples"], [])
        self.assertIsNone(data["best_frame_index"])
        self.assertFalse(data["complete"])

    def test_actual_sample_positions_center_and_maximum_frame(self):
        history = initialize_fine_scan(11105, 5, 2)
        for index, score in enumerate([81, 90, 86, 95, 88]):
            history = append_fine_sharpness(score, history)
            data = json.loads(history)
            self.assertEqual(data["samples"][-1]["position"], 11095 + index * 5)
            self.assertEqual(data["complete"], index == 4)
        self.assertEqual([s["position"] for s in data["samples"]],
                         [11095, 11100, 11105, 11110, 11115])
        result = json.loads(finalize_fine_scan(history))
        self.assertTrue(result["complete"])
        self.assertEqual(result["best_frame_index"], 3)
        self.assertEqual(result["best_frame_number"], 4)
        self.assertEqual(result["best_position_um"], 11110)
        self.assertEqual(result["best_sharpness"], 95)
        self.assertFalse(result["best_at_boundary"])

    def test_image_retention_signal_on_ties_and_nearest_center(self):
        history = initialize_fine_scan(100, 5, 2)
        flags = []
        retained_image = None
        for index, score in enumerate([5, 9, 9, 9, 5]):
            history = append_fine_sharpness(score, history)
            data = json.loads(history)
            flags.append(data["current_is_best"])
            if data["current_is_best"]:
                retained_image = "image_" + str(index)
        self.assertEqual(flags, [True, True, True, False, False])
        self.assertEqual(retained_image, "image_2")
        result = json.loads(finalize_fine_scan(history))
        self.assertEqual(result["best_frame_index"], 2)
        self.assertEqual(result["best_position_um"], 100)

    def test_equal_distance_tie_chooses_earlier_frame(self):
        history = initialize_fine_scan(100, 5, 2)
        for score in [5, 9, 8, 9, 5]:
            history = append_fine_sharpness(score, history)
        result = json.loads(finalize_fine_scan(history))
        self.assertEqual(result["best_frame_index"], 1)
        self.assertEqual(result["best_position_um"], 95)

    def test_higher_score_always_wins_even_at_boundary(self):
        history = initialize_fine_scan(100, 5, 1)
        for score in [10, 9.9999, 7]:
            history = append_fine_sharpness(score, history)
        result = json.loads(finalize_fine_scan(history))
        self.assertEqual(result["best_position_um"], 95)
        self.assertTrue(result["best_at_boundary"])
        self.assertTrue(result["complete"])

    def test_center_only_and_negative_scores(self):
        history = initialize_fine_scan(0, 0.25, 0)
        plan = json.loads(history)
        self.assertEqual(plan["start_position"], -0.25)
        self.assertEqual(plan["end_position"], 0)
        history = append_fine_sharpness(-5, history)
        result = json.loads(finalize_fine_scan(history))
        self.assertEqual(result["sample_count"], 1)
        self.assertEqual(result["best_position_um"], 0)
        self.assertEqual(result["best_frame_index"], 0)

    def test_decimal_positions_and_whole_float_count(self):
        history = initialize_fine_scan(0.3, 0.1, 1.0)
        for score in [1, 3, 2]:
            history = append_fine_sharpness(score, history)
        self.assertEqual([s["position"] for s in json.loads(history)["samples"]], [0.2, 0.3, 0.4])

    def test_travel_limits_include_untriggered_start(self):
        with self.assertRaises(ValueError):
            initialize_fine_scan(100, 5, 2, travel_min_um=90)
        # 拍照从 90 开始，但运动必须先到 85；恰好等于上下限可用。
        plan = json.loads(initialize_fine_scan(100, 5, 2, travel_min_um=85, travel_max_um=110))
        self.assertEqual(plan["start_position"], 85)
        with self.assertRaises(ValueError):
            initialize_fine_scan(100, 5, 2, travel_max_um=109)
        with self.assertRaises(ValueError):
            initialize_fine_scan(100, 5, 2, travel_min_um=120, travel_max_um=90)

    def test_incomplete_overflow_and_missing_initialization_rejected(self):
        history = initialize_fine_scan(100, 5, 0)
        with self.assertRaises(ValueError):
            finalize_fine_scan(history)
        with self.assertRaises(ValueError):
            append_fine_sharpness(5, "")
        history = append_fine_sharpness(5, history)
        with self.assertRaises(ValueError):
            append_fine_sharpness(6, history)

    def test_corrupt_history_and_parameters_rejected(self):
        history = append_fine_sharpness(3, initialize_fine_scan(100, 5, 1))
        for field in ["start_position", "side_count", "frame_index", "position", "complete"]:
            data = json.loads(history)
            if field in ("frame_index", "position"):
                data["samples"][0][field] = 999
            elif field == "complete":
                data[field] = True
            else:
                data[field] = 999
            with self.subTest(field=field), self.assertRaises(ValueError):
                append_fine_sharpness(4, json.dumps(data))

    def test_invalid_parameters_or_scores_rejected(self):
        for args in [(None, 5, 1), (100, 0, 1), (100, -1, 1), (100, 5, -1),
                     (100, 5, 1.5), (100, 5, True), (float("nan"), 5, 1)]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                initialize_fine_scan(*args)
        history = initialize_fine_scan(100, 5, 1)
        for score in [None, True, float("nan"), float("inf"), "bad"]:
            with self.subTest(score=score), self.assertRaises(ValueError):
                append_fine_sharpness(score, history)

    def test_final_result_recomputes_best_from_actual_samples(self):
        history = append_fine_sharpness(7, initialize_fine_scan(100, 5, 0))
        data = json.loads(history)
        data.update({"best_frame_index": 999, "best_position_um": 999, "best_sharpness": 999})
        result = json.loads(finalize_fine_scan(json.dumps(data)))
        self.assertEqual(result["best_frame_index"], 0)
        self.assertEqual(result["best_position_um"], 100)
        self.assertEqual(result["best_sharpness"], 7)

    def test_independent_va_nodes_only_share_json(self):
        path = Path(__file__).parents[1] / "va_scripts" / "fine_scan.py"
        namespace = runpy.run_path(str(path))
        history = namespace["initialize_fine_scan"](11105, 5, 1)
        for score in [70, 90, 80]:
            namespace = runpy.run_path(str(path))
            history = namespace["append_fine_sharpness"](score, history)
        namespace = runpy.run_path(str(path))
        result = json.loads(namespace["finalize_fine_scan"](history))
        self.assertEqual(result["best_position_um"], 11105)
        self.assertEqual(result["best_frame_number"], 2)


if __name__ == "__main__":
    unittest.main()
