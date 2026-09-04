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
    """专用 YOLO Detect 找圆模型的配置。"""

    model_path: str = ""
    confidence_floor: float = 0.25
    expected_circle_count: int = 1


@dataclass
class InspectionConfig:
    """最终成像语义分割质检的独立配置。"""

    enabled: bool = True
    model_path: str = ""
    inference_imgsz: int = 1024
    inference_confidence_floor: float = 0.01
    mm_per_pixel: float = 0.0
    history_root: str = "inspection_history"
    circle: CircleDetectionConfig = field(
        default_factory=CircleDetectionConfig
    )
    region_rules: list[InspectionRegionRule] = field(default_factory=list)
    # 后续以每个检测圆的圆心为中心裁切正方形，各端面共用此边长。
    # 单位为原图像素；不随圆半径或 inference_imgsz 改变，也不附加边距。
    # 1024 是待通过裁切预览确认的初始值。本阶段只持久化，不启用裁切。
    roi_size_px: int = 1024

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

        roi_error = _roi_size_error(self.roi_size_px)
        if roi_error:
            errors.append(roi_error)

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

        roi_error = _roi_size_error(config.roi_size_px)
        if roi_error:
            raise ValueError(roi_error)

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
    roi_size_px = payload.get("roi_size_px", defaults.roi_size_px)
    roi_error = _roi_size_error(roi_size_px)
    if roi_error:
        raise ValueError(roi_error)

    if not isinstance(circle_payload, dict):
        raise ValueError("circle 必须是 JSON 对象")
    if not isinstance(rules_payload, list):
        raise ValueError("region_rules 必须是 JSON 数组")

    circle_defaults = CircleDetectionConfig()
    circle = CircleDetectionConfig(
        model_path=str(circle_payload.get(
            "model_path", circle_defaults.model_path
        )),
        # 旧配置的 min_candidate_score 等价迁移为 YOLO 置信度下限。
        confidence_floor=float(circle_payload.get(
            "confidence_floor",
            circle_payload.get(
                "min_candidate_score", circle_defaults.confidence_floor
            ),
        )),
        expected_circle_count=int(circle_payload.get(
            "expected_circle_count", circle_defaults.expected_circle_count
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
        roi_size_px=roi_size_px,
    )


def _roi_size_error(value: Any) -> str:
    """不将小数截断或把布尔值当成像素数；ROI 边长无需为 32 的倍数。"""

    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return "ROI 边长 roi_size_px 必须是大于 0 的整数（原图像素）"
    return ""


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
    """检查专用 YOLO 找圆配置。"""

    errors = []
    if not str(config.model_path).strip():
        errors.append("找圆模型路径不能为空")
    if config.expected_circle_count < 1:
        errors.append("预期圆数量必须至少为 1")
    if not math.isfinite(config.confidence_floor) or not (
        0 <= config.confidence_floor <= 1
    ):
        errors.append("找圆置信度下限必须在 0～1 之间")
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
