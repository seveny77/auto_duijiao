# -*- coding: utf-8 -*-
"""按区域、污点/划痕类别和尺寸段执行的新版质检规则引擎。"""

import math
from typing import Optional

from backend.defect_measurement import measure_instances
from backend.inspection_types import (
    CircleCandidate,
    InspectionResult,
    InspectionSizeRule,
    InspectionStatus,
    SegmentationInstance,
    SizeRuleInspectionResult,
)


class SizeRuleInspectionEngine:
    """使用测量结果执行 A/B/C/D 尺寸化工艺规则。

    该引擎与旧 ``InspectionRuleEngine`` 独立存在。调用方明确选择新引擎
    后才会使用尺寸规则，因此本模块不会改变当前生产 GUI 的判定结果。
    """

    def reevaluate(
        self,
        source_result: InspectionResult,
        *,
        um_per_pixel: float,
        size_rules: list[InspectionSizeRule],
    ) -> InspectionResult:
        """复用一次推理得到的实例和圆候选，仅重新测量和判定。"""

        if not isinstance(source_result, InspectionResult):
            raise TypeError("source_result 必须是 InspectionResult")
        return self.evaluate(
            instances=list(source_result.instances),
            circle_candidates=list(source_result.circle_candidates),
            selected_circle_index=source_result.selected_circle_index,
            circle_confirmed=source_result.circle_confirmed,
            um_per_pixel=um_per_pixel,
            size_rules=size_rules,
            image_width=source_result.image_width,
            image_height=source_result.image_height,
        )

    def evaluate(
        self,
        *,
        instances: list[SegmentationInstance],
        circle_candidates: list[CircleCandidate],
        selected_circle_index: Optional[int],
        circle_confirmed: bool,
        um_per_pixel: float,
        size_rules: list[InspectionSizeRule],
        image_width: int = 0,
        image_height: int = 0,
    ) -> InspectionResult:
        """按当前确认圆心和尺寸规则返回完整、可序列化的检测结果。"""

        result = InspectionResult(
            status=InspectionStatus.PENDING,
            image_width=int(image_width),
            image_height=int(image_height),
            mm_per_pixel=float(um_per_pixel),
            circle_candidates=list(circle_candidates),
            selected_circle_index=selected_circle_index,
            circle_confirmed=bool(circle_confirmed),
            instances=list(instances),
        )
        prerequisite_error = _prerequisite_error(
            result=result,
            size_rules=size_rules,
        )
        if prerequisite_error:
            result.failure_reasons.append(prerequisite_error)
            return result

        result.measurements = measure_instances(
            instances,
            um_per_pixel=um_per_pixel,
        )
        rule_results = {
            rule.rule_id: _new_rule_result(rule)
            for rule in size_rules
        }
        regions = _ordered_regions(size_rules)
        selected_circle = circle_candidates[selected_circle_index]
        unresolved = False

        for measurement in result.measurements:
            instance = instances[measurement.instance_index]
            if measurement.source == "invalid":
                unresolved = True
                result.warnings.append(
                    f"分割实例[{measurement.instance_index}]无法测量物理尺寸"
                )
                continue

            defect_class = str(instance.class_name).strip()
            if defect_class not in {"污点", "划痕"}:
                unresolved = True
                result.warnings.append(
                    f"分割实例[{measurement.instance_index}]类别“{defect_class or '空'}”"
                    "不是污点或划痕"
                )
                continue

            if measurement.center_x_px is None or measurement.center_y_px is None:
                unresolved = True
                result.warnings.append(
                    f"分割实例[{measurement.instance_index}]没有有效质心"
                )
                continue

            distance_um = math.hypot(
                measurement.center_x_px - selected_circle.center_x,
                measurement.center_y_px - selected_circle.center_y,
            ) * um_per_pixel
            region = _locate_region(distance_um, regions)
            if region is None:
                # 圆环定义外的实例不属于任何工艺区，保留在原始实例结果中，
                # 但不参与本次 A/B/C/D 规则统计。
                continue

            size_value = _measurement_value(defect_class, measurement)
            if size_value is None:
                unresolved = True
                result.warnings.append(
                    f"分割实例[{measurement.instance_index}]无法取得"
                    f"{defect_class}判定尺寸"
                )
                continue

            matched_rule = _find_matching_rule(
                size_rules=size_rules,
                region_id=region[0],
                defect_class=defect_class,
                size_value_um=size_value,
            )
            if matched_rule is None:
                unresolved = True
                result.warnings.append(
                    f"区域“{region[1]}”的{defect_class}尺寸"
                    f" {size_value:.3f} µm 未匹配到规则"
                )
                continue

            rule_result = rule_results[matched_rule.rule_id]
            rule_result.actual_instance_count += 1
            rule_result.instance_indices.append(measurement.instance_index)
            if measurement.estimated:
                rule_result.estimated_instance_count += 1

        for rule in size_rules:
            rule_result = rule_results[rule.rule_id]
            if (
                rule.max_instance_count is not None
                and rule_result.actual_instance_count > rule.max_instance_count
            ):
                rule_result.passed = False
                failure = _failure_reason(rule_result)
                rule_result.failure_reasons.append(failure)
                result.failure_reasons.append(failure)
            result.size_rule_results.append(rule_result)

        if result.failure_reasons:
            result.status = InspectionStatus.FAIL
        elif unresolved:
            result.status = InspectionStatus.PENDING
        else:
            result.status = InspectionStatus.PASS
        return result


def size_rule_matches(rule: InspectionSizeRule, size_value_um: float) -> bool:
    """判断尺寸值是否属于规则段，供引擎和边界值测试共同使用。"""

    if not math.isfinite(size_value_um) or size_value_um < 0:
        return False
    lower_ok = (
        size_value_um >= rule.min_size_um
        if rule.min_inclusive else size_value_um > rule.min_size_um
    )
    if not lower_ok:
        return False
    if rule.max_size_um is None:
        return True
    return (
        size_value_um <= rule.max_size_um
        if rule.max_inclusive else size_value_um < rule.max_size_um
    )


def _prerequisite_error(
    *,
    result: InspectionResult,
    size_rules: list[InspectionSizeRule],
) -> str:
    if not math.isfinite(result.mm_per_pixel) or result.mm_per_pixel <= 0:
        return "像素标定比例 um_per_pixel 必须大于 0"
    if not result.circle_candidates:
        return "没有可用的候选圆"
    if result.selected_circle_index is None:
        return "尚未选择候选圆"
    if not 0 <= result.selected_circle_index < len(result.circle_candidates):
        return "选中的候选圆序号无效"
    if not result.circle_confirmed:
        return "圆心尚未确认"
    if not size_rules:
        return "尚未配置尺寸化判定规则"
    return ""


def _new_rule_result(rule: InspectionSizeRule) -> SizeRuleInspectionResult:
    return SizeRuleInspectionResult(
        rule_id=rule.rule_id,
        region_id=rule.region_id,
        region_name=rule.region_name,
        defect_class=rule.defect_class,
        measurement=rule.measurement,
        min_size_um=rule.min_size_um,
        max_size_um=rule.max_size_um,
        min_inclusive=rule.min_inclusive,
        max_inclusive=rule.max_inclusive,
        max_instance_count=rule.max_instance_count,
    )


def _ordered_regions(
    rules: list[InspectionSizeRule],
) -> list[tuple[str, str, float, float]]:
    regions: dict[str, tuple[str, str, float, float]] = {}
    for rule in rules:
        regions.setdefault(
            rule.region_id,
            (
                rule.region_id,
                rule.region_name,
                rule.inner_radius_um,
                rule.outer_radius_um,
            ),
        )
    return sorted(regions.values(), key=lambda item: item[2])


def _locate_region(
    distance_um: float,
    regions: list[tuple[str, str, float, float]],
) -> tuple[str, str, float, float] | None:
    last_index = len(regions) - 1
    for index, region in enumerate(regions):
        _region_id, _region_name, inner_radius, outer_radius = region
        if inner_radius <= distance_um < outer_radius:
            return region
        if index == last_index and math.isclose(
            distance_um,
            outer_radius,
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            return region
    return None


def _measurement_value(defect_class: str, measurement) -> float | None:
    if defect_class == "污点":
        return measurement.equivalent_diameter_um
    if defect_class == "划痕":
        return measurement.rotated_width_um
    return None


def _find_matching_rule(
    *,
    size_rules: list[InspectionSizeRule],
    region_id: str,
    defect_class: str,
    size_value_um: float,
) -> InspectionSizeRule | None:
    for rule in size_rules:
        if (
            rule.region_id == region_id
            and rule.defect_class == defect_class
            and size_rule_matches(rule, size_value_um)
        ):
            return rule
    return None


def _failure_reason(rule_result: SizeRuleInspectionResult) -> str:
    if rule_result.max_size_um is None:
        size_text = f"> {_format_size(rule_result.min_size_um)}"
        if rule_result.min_inclusive:
            size_text = f">= {_format_size(rule_result.min_size_um)}"
    elif math.isclose(rule_result.min_size_um, 0.0, abs_tol=1e-12):
        operator = "<=" if rule_result.max_inclusive else "<"
        size_text = f"{operator} {_format_size(rule_result.max_size_um)}"
    else:
        left = "<=" if rule_result.min_inclusive else "<"
        right = "<=" if rule_result.max_inclusive else "<"
        size_text = (
            f"{_format_size(rule_result.min_size_um)} {left} 尺寸"
            f" {right} {_format_size(rule_result.max_size_um)}"
        )
    return (
        f"{rule_result.region_name}/{rule_result.defect_class}"
        f" {size_text} µm 数量 {rule_result.actual_instance_count}"
        f" 超过上限 {rule_result.max_instance_count}"
    )


def _format_size(value: float) -> str:
    return f"{value:g}"
