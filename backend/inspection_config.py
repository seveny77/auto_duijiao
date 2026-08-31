# -*- coding: utf-8 -*-
"""语义分割质检配置及其 JSON 持久化。"""

from dataclasses import dataclass, field
import json
import math
import os
from typing import Any

from backend.inspection_types import (
    InspectionRegionRule,
    inspection_to_dict,
)


@dataclass
class CircleDetectionConfig:
    """自动找圆算法的可调参数。"""

    downsample_factor: int = 4
    blur_kernel_size: int = 9
    hough_dp: float = 1.2
    hough_param1: float = 100.0
    hough_param2: float = 30.0
    min_center_distance_px: float = 100.0
    min_radius_px: int = 100
    max_radius_px: int = 2000
    expected_circle_count: int = 1
    min_candidate_score: float = 0.50


@dataclass
class InspectionConfig:
    """最终成像语义分割质检的独立配置。"""

    enabled: bool = True
    model_path: str = ""
    inference_imgsz: int = 4096
    inference_confidence_floor: float = 0.01
    mm_per_pixel: float = 0.0
    history_root: str = "inspection_history"
    circle: CircleDetectionConfig = field(
        default_factory=CircleDetectionConfig
    )
    region_rules: list[InspectionRegionRule] = field(default_factory=list)

    def validate(self) -> list[str]:
        """返回全部配置错误；空列表表示配置可用于检测。"""

        errors: list[str] = []

        if not math.isfinite(self.mm_per_pixel) or self.mm_per_pixel <= 0:
            errors.append("像素标定比例 mm_per_pixel 必须大于 0")

        if not str(self.history_root).strip():
            errors.append("历史记录目录不能为空")

        if self.inference_imgsz < 1:
            errors.append("分割推理尺寸 inference_imgsz 必须大于 0")
        if not math.isfinite(self.inference_confidence_floor) or not (
            0 <= self.inference_confidence_floor <= 1
        ):
            errors.append("分割推理置信度下限必须在 0～1 之间")

        errors.extend(_validate_circle_config(self.circle))
        errors.extend(_validate_region_rules(self.region_rules))
        return errors

    def validate_evaluation(self) -> list[str]:
        """只校验当前图规则复判直接依赖的比例和区域规则。"""

        errors = []
        if not math.isfinite(self.mm_per_pixel) or self.mm_per_pixel <= 0:
            errors.append("像素标定比例 mm_per_pixel 必须大于 0")
        errors.extend(_validate_region_rules(self.region_rules))
        return errors


class InspectionConfigStore:
    """负责将检测配置保存到独立 JSON，或从中恢复。"""

    def __init__(self, path: str):
        self._path = os.path.abspath(path)

    @property
    def path(self) -> str:
        return self._path

    def load(self) -> InspectionConfig:
        """读取配置；文件不存在时返回一份全新默认配置。"""

        if not os.path.exists(self._path):
            return InspectionConfig()

        with open(self._path, "r", encoding="utf-8") as file:
            payload = json.load(file)

        if not isinstance(payload, dict):
            raise ValueError("检测配置 JSON 顶层必须是对象")

        return inspection_config_from_dict(payload)

    def save(self, config: InspectionConfig):
        """原子保存配置，避免中途退出留下不完整 JSON。"""

        parent = os.path.dirname(self._path)
        if parent:
            os.makedirs(parent, exist_ok=True)

        temporary_path = f"{self._path}.tmp"

        try:
            with open(temporary_path, "w", encoding="utf-8") as file:
                json.dump(
                    inspection_to_dict(config),
                    file,
                    ensure_ascii=False,
                    indent=2,
                )
                file.write("\n")

            os.replace(temporary_path, self._path)
        finally:
            if os.path.exists(temporary_path):
                os.remove(temporary_path)


def inspection_config_from_dict(payload: dict[str, Any]) -> InspectionConfig:
    """从 JSON 字典恢复配置，并兼容缺少新增字段的旧版本。"""

    defaults = InspectionConfig()
    circle_payload = payload.get("circle", {})
    rules_payload = payload.get("region_rules", [])

    if not isinstance(circle_payload, dict):
        raise ValueError("circle 必须是 JSON 对象")
    if not isinstance(rules_payload, list):
        raise ValueError("region_rules 必须是 JSON 数组")

    circle_defaults = CircleDetectionConfig()
    circle = CircleDetectionConfig(
        downsample_factor=int(circle_payload.get(
            "downsample_factor", circle_defaults.downsample_factor
        )),
        blur_kernel_size=int(circle_payload.get(
            "blur_kernel_size", circle_defaults.blur_kernel_size
        )),
        hough_dp=float(circle_payload.get("hough_dp", circle_defaults.hough_dp)),
        hough_param1=float(circle_payload.get(
            "hough_param1", circle_defaults.hough_param1
        )),
        hough_param2=float(circle_payload.get(
            "hough_param2", circle_defaults.hough_param2
        )),
        min_center_distance_px=float(circle_payload.get(
            "min_center_distance_px",
            circle_defaults.min_center_distance_px,
        )),
        min_radius_px=int(circle_payload.get(
            "min_radius_px", circle_defaults.min_radius_px
        )),
        max_radius_px=int(circle_payload.get(
            "max_radius_px", circle_defaults.max_radius_px
        )),
        expected_circle_count=int(circle_payload.get(
            "expected_circle_count", circle_defaults.expected_circle_count
        )),
        min_candidate_score=float(circle_payload.get(
            "min_candidate_score", circle_defaults.min_candidate_score
        )),
    )

    rules = []
    for index, item in enumerate(rules_payload):
        if not isinstance(item, dict):
            raise ValueError(f"region_rules[{index}] 必须是 JSON 对象")
        rules.append(_region_rule_from_dict(item))

    return InspectionConfig(
        enabled=bool(payload.get("enabled", defaults.enabled)),
        model_path=str(payload.get("model_path", defaults.model_path)),
        inference_imgsz=int(payload.get(
            "inference_imgsz", defaults.inference_imgsz
        )),
        inference_confidence_floor=float(payload.get(
            "inference_confidence_floor",
            defaults.inference_confidence_floor,
        )),
        mm_per_pixel=float(payload.get(
            "mm_per_pixel", defaults.mm_per_pixel
        )),
        history_root=str(payload.get("history_root", defaults.history_root)),
        circle=circle,
        region_rules=rules,
    )


def _region_rule_from_dict(payload: dict[str, Any]) -> InspectionRegionRule:
    """从一条 JSON 规则恢复类型化区域规则。"""

    return InspectionRegionRule(
        region_id=str(payload.get("region_id", "")),
        region_name=str(payload.get("region_name", "")),
        inner_radius_mm=float(payload.get("inner_radius_mm", 0.0)),
        outer_radius_mm=float(payload.get("outer_radius_mm", 0.0)),
        class_id=int(payload.get("class_id", -1)),
        class_name=str(payload.get("class_name", "")),
        min_confidence=float(payload.get("min_confidence", 0.0)),
        min_instance_area_mm2=float(payload.get(
            "min_instance_area_mm2", 0.0
        )),
        max_instance_count=int(payload.get("max_instance_count", 0)),
    )


def _validate_circle_config(config: CircleDetectionConfig) -> list[str]:
    """检查找圆参数的数值范围。"""

    errors = []
    if config.downsample_factor < 1:
        errors.append("找圆降采样倍数必须至少为 1")
    if config.blur_kernel_size < 1 or config.blur_kernel_size % 2 == 0:
        errors.append("找圆模糊核尺寸必须是正奇数")
    if not math.isfinite(config.hough_dp) or config.hough_dp <= 0:
        errors.append("Hough dp 必须大于 0")
    if not math.isfinite(config.hough_param1) or config.hough_param1 <= 0:
        errors.append("Hough param1 必须大于 0")
    if not math.isfinite(config.hough_param2) or config.hough_param2 <= 0:
        errors.append("Hough param2 必须大于 0")
    if config.min_center_distance_px <= 0:
        errors.append("候选圆心最小距离必须大于 0")
    if config.min_radius_px < 0:
        errors.append("候选圆最小半径不能小于 0")
    if config.max_radius_px <= config.min_radius_px:
        errors.append("候选圆最大半径必须大于最小半径")
    if config.expected_circle_count < 1:
        errors.append("预期圆数量必须至少为 1")
    if not 0 <= config.min_candidate_score <= 1:
        errors.append("候选圆最低评分必须在 0～1 之间")
    return errors


def _validate_region_rules(rules: list[InspectionRegionRule]) -> list[str]:
    """检查区域规则、重复项和同心圆环连续性。"""

    errors = []
    seen_pairs = set()
    region_specs: dict[str, tuple[str, float, float]] = {}

    for index, rule in enumerate(rules):
        label = f"区域规则[{index}]"
        if not rule.region_id.strip():
            errors.append(f"{label} region_id 不能为空")
        if not rule.region_name.strip():
            errors.append(f"{label} region_name 不能为空")
        if rule.inner_radius_mm < 0:
            errors.append(f"{label} 内半径不能小于 0")
        if rule.outer_radius_mm <= rule.inner_radius_mm:
            errors.append(f"{label} 外半径必须大于内半径")
        if rule.class_id < 0:
            errors.append(f"{label} class_id 不能小于 0")
        if not rule.class_name.strip():
            errors.append(f"{label} class_name 不能为空")
        if not 0 <= rule.min_confidence <= 1:
            errors.append(f"{label} 最低置信度必须在 0～1 之间")
        if rule.min_instance_area_mm2 < 0:
            errors.append(f"{label} 最小实例面积不能小于 0")
        if rule.max_instance_count < 0:
            errors.append(f"{label} 数量上限不能小于 0")

        pair = (rule.region_id, rule.class_id)
        if pair in seen_pairs:
            errors.append(
                f"区域 {rule.region_id} 的类别 {rule.class_id} 规则重复"
            )
        seen_pairs.add(pair)

        spec = (
            rule.region_name,
            rule.inner_radius_mm,
            rule.outer_radius_mm,
        )
        previous_spec = region_specs.get(rule.region_id)
        if previous_spec is not None and previous_spec != spec:
            errors.append(f"区域 {rule.region_id} 的名称或半径定义不一致")
        else:
            region_specs[rule.region_id] = spec

    ordered_regions = sorted(
        region_specs.items(),
        key=lambda item: item[1][1],
    )
    if ordered_regions:
        first_id, first_spec = ordered_regions[0]
        if not math.isclose(first_spec[1], 0.0, abs_tol=1e-9):
            errors.append(f"首个圆环 {first_id} 的内半径必须为 0")

        for (previous_id, previous), (current_id, current) in zip(
                ordered_regions,
                ordered_regions[1:],
        ):
            if not math.isclose(previous[2], current[1], abs_tol=1e-9):
                errors.append(
                    f"圆环 {previous_id} 与 {current_id} 之间存在空隙或重叠"
                )

    return errors
