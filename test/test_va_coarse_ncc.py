"""VA 数据契约及已知位移曲线的 NCC 回归验证，不访问相机、VA 或运动硬件。"""

import json
import math
import unittest
from pathlib import Path

from va_scripts.calibration_sharpness import initialize_calibration, append_sharpness
from va_scripts.coarse_scan import initialize_coarse_scan, append_coarse_sharpness
from va_scripts.coarse_ncc import calculate_coarse_ncc


def curve(position):
    return (10 + 100 * math.exp(-((position - 100) / 16) ** 2)
            + 16 * math.exp(-((position - 68) / 8) ** 2))


def build_scan(start, end, step, score_function, coarse=False):
    initialize = initialize_coarse_scan if coarse else initialize_calibration
    append = append_coarse_sharpness if coarse else append_sharpness
    result = initialize(start, end, step)
    direction = 1 if end > start else -1
    for index in range(1, round(abs(end - start) / step) + 1):
        position = start + direction * step * index
        result = append(score_function(position), start, end, step, result)
    return result


class CoarseScanTests(unittest.TestCase):
    def test_start_excluded_endpoint_included_and_overflow_rejected(self):
        for start, end, expected in [(100, 130, [110, 120, 130]),
                                      (130, 100, [120, 110, 100])]:
            with self.subTest(start=start):
                history = initialize_coarse_scan(start, end, 10)
                self.assertEqual(json.loads(history)["samples"], [])
                for index, score in enumerate([12.3, 45.6, 38.2]):
                    history = append_coarse_sharpness(score, start, end, 10, history)
                    self.assertEqual(json.loads(history)["complete"], index == 2)
                data = json.loads(history)
                self.assertEqual(data["scan_type"], "coarse")
                self.assertEqual([item["position"] for item in data["samples"]], expected)
                with self.assertRaises(ValueError):
                    append_coarse_sharpness(10, start, end, 10, history)

    def test_calibration_not_overwritten_and_parameter_changes_rejected(self):
        history = initialize_calibration(0, 100, 10)
        with self.assertRaises(ValueError):
            append_coarse_sharpness(5, 0, 100, 10, history)
        history = append_coarse_sharpness(5, 0, 100, 10)
        with self.assertRaises(ValueError):
            append_coarse_sharpness(6, 0, 200, 10, history)

    def test_fractional_positions(self):
        data = json.loads(build_scan(0, 0.3, 0.1, lambda z: z, coarse=True))
        self.assertEqual([item["position"] for item in data["samples"]], [0.1, 0.2, 0.3])


class CoarseNCCTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.calibration = build_scan(0, 200, 2, curve)

    def match(self, coarse, calibration=None, **options):
        return json.loads(calculate_coarse_ncc(
            self.calibration if calibration is None else calibration, coarse, **options))

    def test_known_shifts_with_gain_and_offset(self):
        for shift in [-14, 0, 14]:
            with self.subTest(shift=shift):
                coarse = build_scan(0, 200, 10, lambda z: 3.7 * curve(z - shift) + 23, True)
                result = self.match(coarse)
                self.assertTrue(result["valid"], result)
                self.assertEqual(result["shift_um"], shift)
                self.assertEqual(result["predicted_peak_um"], 100 + shift)
                self.assertAlmostEqual(result["ncc_max"], 1)

    def test_reverse_calibration_and_coarse_scans(self):
        calibration = build_scan(200, 0, 2, curve)
        coarse = build_scan(200, 0, 10, lambda z: curve(z - 14), True)
        result = self.match(coarse, calibration)
        self.assertTrue(result["valid"], result)
        self.assertEqual(result["predicted_peak_um"], 114)

    def test_different_grid_origins_and_linear_interpolation(self):
        # 三角形的转折点都落在标定网格上，粗扫点在网格之间，线性插值应精确。
        def triangle(z):
            return 10 + max(0, 100 - 2.5 * abs(z - 100))
        calibration = build_scan(0, 200, 4, triangle)
        coarse = build_scan(5, 205, 10, lambda z: triangle(z - 9), True)
        result = self.match(coarse, calibration, match_step_um=1)
        self.assertTrue(result["valid"], result)
        self.assertEqual(result["predicted_peak_um"], 109)
        self.assertAlmostEqual(result["ncc_max"], 1)

    def test_fractional_shift_resolution(self):
        calibration = build_scan(0, 20, 0.25, lambda z: curve(z * 10))
        coarse = build_scan(0, 20, 1, lambda z: curve((z - 1.25) * 10), True)
        result = self.match(coarse, calibration)
        self.assertTrue(result["valid"], result)
        self.assertEqual(result["predicted_peak_um"], 11.25)

    def test_incomplete_or_corrupt_data_rejected(self):
        coarse = build_scan(0, 200, 10, curve, True)
        for name in ["complete", "position", "sharpness", "position_unit"]:
            data = json.loads(coarse)
            if name == "complete":
                data[name] = False
            elif name == "position":
                data["samples"][0][name] = 0  # 不能将起点冒充第一帧。
            elif name == "sharpness":
                data["samples"][0][name] = float("nan")
            else:
                data[name] = "mm"
            with self.subTest(field=name), self.assertRaises(ValueError):
                self.match(json.dumps(data))

    def test_flat_curve_and_too_few_points(self):
        flat = self.match(build_scan(0, 200, 10, lambda z: 5, True))
        self.assertEqual(flat["quality"], "flat_curve")
        self.assertFalse(flat["valid"])
        self.assertIsNone(flat["predicted_peak_um"])
        short = self.match(build_scan(0, 200, 50, curve, True))
        self.assertEqual(short["quality"], "insufficient_points")

    def test_insufficient_overlap(self):
        calibration = build_scan(80, 120, 2, curve)
        result = self.match(build_scan(0, 400, 10, curve, True), calibration)
        self.assertEqual(result["quality"], "no_match")
        self.assertFalse(result["valid"])

    def test_boundary_peak_does_not_pass(self):
        coarse = build_scan(0, 200, 10, lambda z: curve(z - 96), True)
        result = self.match(coarse, min_overlap_ratio=0.4)
        self.assertEqual(result["quality"], "boundary", result)
        self.assertFalse(result["valid"])

    def test_repeated_shape_is_ambiguous(self):
        def repeated(z):
            return 30 + 10 * math.cos(2 * math.pi * (z - 100) / 40) + math.exp(-(z - 100) ** 2)
        calibration = build_scan(0, 200, 2, repeated)
        result = self.match(build_scan(0, 200, 10, repeated, True), calibration)
        self.assertEqual(result["quality"], "ambiguous", result)
        self.assertFalse(result["valid"])

    def test_wrong_shape_is_low_ncc(self):
        coarse = build_scan(0, 200, 10, lambda z: 100 if round(z / 10) % 2 else 0, True)
        result = self.match(coarse)
        self.assertEqual(result["quality"], "low_ncc", result)
        self.assertFalse(result["valid"])

    def test_template_boundary(self):
        coarse = build_scan(0, 200, 10, curve, True)
        boundary = self.match(coarse, build_scan(0, 200, 2, lambda z: z))
        self.assertEqual(boundary["quality"], "template_boundary")

    def test_contiguous_plateau_uses_midpoint_and_exposes_range(self):
        def plateau(z):
            return min(curve(z), 90)
        calibration = build_scan(0, 200, 2, plateau)
        coarse = build_scan(0, 200, 10, lambda z: plateau(z - 14), True)
        result = self.match(coarse, calibration)
        self.assertTrue(result["valid"], result)
        self.assertEqual(result["template_peak_method"], "plateau_midpoint")
        lo, hi = result["template_peak_range_um"]
        self.assertLess(lo, hi)
        self.assertEqual(result["template_peak_um"], (lo + hi) / 2)
        self.assertEqual(result["shift_um"], 14)
        self.assertEqual(result["predicted_peak_range_um"], [lo + 14, hi + 14])
        self.assertEqual(result["template_peak_valley_ratio"], 0)

    def test_separated_equal_maxima_still_rejected(self):
        def twin_peaks(z):
            return 10 + max(0, 100 - 5 * abs(z - 60), 100 - 5 * abs(z - 140))
        calibration = build_scan(0, 200, 2, twin_peaks)
        result = self.match(build_scan(0, 200, 10, twin_peaks, True), calibration)
        self.assertFalse(result["valid"])
        self.assertEqual(result["quality"], "ambiguous_template")
        self.assertEqual(result["template_peak_method"], "unresolved_multiple_peaks")
        self.assertIsNone(result["template_peak_um"])
        self.assertIsNone(result["predicted_peak_um"])

    def test_plateau_touching_template_edge_still_rejected(self):
        def edge_plateau(z):
            return min(z, 160)
        calibration = build_scan(0, 200, 2, edge_plateau)
        result = self.match(build_scan(0, 200, 10, edge_plateau, True), calibration)
        self.assertFalse(result["valid"])
        self.assertEqual(result["quality"], "template_boundary")

    def test_user_quantized_plateau_is_diagnosed_but_low_ncc_stays_invalid(self):
        folder = Path(__file__).parent / "fixtures" / "va_coarse_ncc"
        calibration = (folder / "quantized_calibration.json").read_text(encoding="utf-8")
        coarse = (folder / "noisy_coarse.json").read_text(encoding="utf-8")
        result = self.match(coarse, calibration)
        self.assertEqual(result["template_peak_count"], 12)
        self.assertEqual(result["template_peak_range_um"], [10550, 10620])
        self.assertEqual(result["template_peak_um"], 10585)
        self.assertAlmostEqual(result["template_peak_valley_ratio"], 1 / 30)
        self.assertEqual(result["quality"], "low_ncc")
        self.assertFalse(result["valid"])
        self.assertAlmostEqual(result["ncc_max"], 0.6520666465809992)
        self.assertEqual(result["predicted_peak_um"], 10525)
        self.assertEqual(result["predicted_peak_range_um"], [10490, 10560])
        self.assertEqual(result["coarse_best_position_um"], 10600)
        self.assertAlmostEqual(result["ncc_gap"], 0.002610343172620322)
        # 即使人为降低 NCC 门槛，这组数据也必须被边界/覆盖检查拒绝。
        self.assertFalse(self.match(coarse, calibration, min_ncc=0.5)["valid"])
        # 允许按现场需求关闭浅谷合并，不会对原始样本加扰动或改值。
        strict = self.match(coarse, calibration, max_peak_valley_ratio=0)
        self.assertEqual(strict["quality"], "ambiguous_template")

    def test_plateau_merge_is_invariant_to_gain_and_offset(self):
        folder = Path(__file__).parent / "fixtures" / "va_coarse_ncc"
        calibration = json.loads((folder / "quantized_calibration.json").read_text(encoding="utf-8"))
        coarse = (folder / "noisy_coarse.json").read_text(encoding="utf-8")
        for sample in calibration["samples"]:
            sample["sharpness"] = 2 * sample["sharpness"] + 500
        result = self.match(coarse, json.dumps(calibration))
        self.assertEqual(result["template_peak_um"], 10585)
        self.assertEqual(result["template_peak_method"], "plateau_midpoint")
        self.assertEqual(result["quality"], "low_ncc")
        self.assertAlmostEqual(result["ncc_max"], 0.6520666465809992)

    def test_no_zero_padding_for_out_of_range_template_points(self):
        coarse = build_scan(0, 200, 10, lambda z: curve(z - 14), True)
        result = self.match(coarse)
        self.assertEqual(result["matched_points"], 19)  # 第一帧映射到 -4，不参与相关。
        self.assertEqual(result["overlap_ratio"], 0.95)

    def test_invalid_matching_parameters(self):
        coarse = build_scan(0, 200, 10, curve, True)
        for options in [{"min_points": 2}, {"min_overlap_ratio": 0},
                        {"match_step_um": 0}, {"min_ncc": float("nan")},
                        {"match_step_um": 0.00001}, {"max_peak_valley_ratio": 1},
                        {"max_peak_valley_ratio": -0.1}]:
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.match(coarse, **options)


if __name__ == "__main__":
    unittest.main()
