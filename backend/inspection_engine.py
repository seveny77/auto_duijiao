# -*- coding: utf-8 -*-
"""基于缺陷质心和同心圆环的纯 Python 质检规则引擎。"""

import math
from typing import Optional

from backend.inspection_types import (
    CircleCandidate,
    InspectionRegionRule,
    InspectionResult,
    InspectionStatus,
    RegionInspectionResult,
    SegmentationInstance,
)


class InspectionRuleEngine:
    """按照缺陷质心所属圆环统计有效实例并完成数量判定。"""

    def reevaluate(
        self,
        source_result: InspectionResult,
        *,
        mm_per_pixel: float,
        region_rules: list[InspectionRegionRule],
    ) -> InspectionResult:
        """复用一次检测的实例和圆候选，只重新执行统计判定。"""

        if not isinstance(source_result, InspectionResult):
            raise TypeError("source_result 必须是 InspectionResult")
        return self.evaluate(
            instances=list(source_result.instances),
            circle_candidates=list(source_result.circle_candidates),
            selected_circle_index=source_result.selected_circle_index,
            circle_confirmed=source_result.circle_confirmed,
            mm_per_pixel=mm_per_pixel,
            region_rules=region_rules,
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
        mm_per_pixel: float,
        region_rules: list[InspectionRegionRule],
        image_width: int = 0,
        image_height: int = 0,
    ) -> InspectionResult:
        """返回当前圆心、比例和规则下的完整质检结果。"""

        result = InspectionResult(
            status=InspectionStatus.PENDING,
            image_width=int(image_width),
            image_height=int(image_height),
            mm_per_pixel=float(mm_per_pixel),
            circle_candidates=list(circle_candidates),
            selected_circle_index=selected_circle_index,
            circle_confirmed=bool(circle_confirmed),
            instances=list(instances),
        )

        prerequisite_error = self._prerequisite_error(
            result,
            region_rules,
        )
        if prerequisite_error:
            result.failure_reasons.append(prerequisite_error)
            return result

        selected_circle = circle_candidates[selected_circle_index]
        ordered_regions = _ordered_regions(region_rules)
        rule_by_pair = {
            (rule.region_id, rule.class_id): rule
            for rule in region_rules
        }
        result_by_pair = {
            (rule.region_id, rule.class_id): RegionInspectionResult(
                region_id=rule.region_id,
                region_name=rule.region_name,
                class_id=rule.class_id,
                class_name=rule.class_name,
            )
            for rule in region_rules
        }

        missing_rule = False
        for index, instance in enumerate(instances):
            centroid = _instance_centroid(instance)
            if centroid is None:
                result.warnings.append(
                    f"分割实例[{index}]无法计算有效质心，已忽略"
                )
                continue

            distance_px = math.hypot(
                centroid[0] - selected_circle.center_x,
                centroid[1] - selected_circle.center_y,
            )
            distance_mm = distance_px * mm_per_pixel
            region = _locate_region(distance_mm, ordered_regions)
            if region is None:
                continue

            region_id, region_name, _inner_radius, _outer_radius = region
            rule = rule_by_pair.get((region_id, instance.class_id))
            if rule is None:
                missing_rule = True
                class_label = instance.class_name or str(instance.class_id)
                warning = (
                    f"区域“{region_name}”缺少类别“{class_label}”的判定规则"
                )
                if warning not in result.warnings:
                    result.warnings.append(warning)
                continue

            if instance.confidence < rule.min_confidence:
                continue

            instance_area_mm2 = (
                max(0, int(instance.pixel_area))
                * mm_per_pixel
                * mm_per_pixel
            )
            if instance_area_mm2 < rule.min_instance_area_mm2:
                continue

            region_result = result_by_pair[(region_id, instance.class_id)]
            region_result.valid_instance_count += 1
            region_result.total_area_mm2 += instance_area_mm2

        for rule in region_rules:
            region_result = result_by_pair[(rule.region_id, rule.class_id)]
            if region_result.valid_instance_count > rule.max_instance_count:
                region_result.passed = False
                reason = (
                    f"{rule.region_name}/{rule.class_name}有效缺陷数量"
                    f" {region_result.valid_instance_count} 超过上限"
                    f" {rule.max_instance_count}"
                )
                region_result.failure_reasons.append(reason)
                result.failure_reasons.append(reason)
            result.region_results.append(region_result)

        if result.failure_reasons:
            result.status = InspectionStatus.FAIL
        elif missing_rule:
            result.status = InspectionStatus.PENDING
        else:
            result.status = InspectionStatus.PASS

        return result

    @staticmethod
    def _prerequisite_error(
        result: InspectionResult,
        region_rules: list[InspectionRegionRule],
    ) -> str:
        if not math.isfinite(result.mm_per_pixel) or result.mm_per_pixel <= 0:
            return "像素标定比例 mm_per_pixel 必须大于 0"
        if not result.circle_candidates:
            return "没有可用的候选圆"
        if result.selected_circle_index is None:
            return "尚未选择候选圆"
        if not 0 <= result.selected_circle_index < len(result.circle_candidates):
            return "选中的候选圆序号无效"
        if not result.circle_confirmed:
            return "圆心尚未确认"
        if not region_rules:
            return "尚未配置圆环判定规则"
        return ""


def _ordered_regions(
    rules: list[InspectionRegionRule],
) -> list[tuple[str, str, float, float]]:
    """从按类别重复的规则中提取唯一、按内半径排序的圆环。"""

    regions = {}
    for rule in rules:
        regions.setdefault(
            rule.region_id,
            (
                rule.region_id,
                rule.region_name,
                rule.inner_radius_mm,
                rule.outer_radius_mm,
            ),
        )
    return sorted(regions.values(), key=lambda item: item[2])


def _locate_region(
    distance_mm: float,
    regions: list[tuple[str, str, float, float]],
) -> Optional[tuple[str, str, float, float]]:
    """按内含外不含规则定位圆环；最外环包含最终外边界。"""

    last_index = len(regions) - 1
    for index, region in enumerate(regions):
        _region_id, _region_name, inner_radius, outer_radius = region
        if inner_radius <= distance_mm < outer_radius:
            return region
        if index == last_index and math.isclose(
            distance_mm,
            outer_radius,
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            return region
    return None


def _instance_centroid(
    instance: SegmentationInstance,
) -> Optional[tuple[float, float]]:
    """优先计算多边形几何质心，无效时回退到 bbox 中心。"""

    polygon = instance.polygon
    if len(polygon) >= 3 and all(
        len(point) == 2
        and math.isfinite(point[0])
        and math.isfinite(point[1])
        for point in polygon
    ):
        cross_sum = 0.0
        center_x_sum = 0.0
        center_y_sum = 0.0
        for current, following in zip(
            polygon,
            polygon[1:] + polygon[:1],
        ):
            cross = (
                current[0] * following[1]
                - following[0] * current[1]
            )
            cross_sum += cross
            center_x_sum += (current[0] + following[0]) * cross
            center_y_sum += (current[1] + following[1]) * cross

        if not math.isclose(cross_sum, 0.0, abs_tol=1e-12):
            center_x = center_x_sum / (3.0 * cross_sum)
            center_y = center_y_sum / (3.0 * cross_sum)
            if math.isfinite(center_x) and math.isfinite(center_y):
                return center_x, center_y

    x1, y1, x2, y2 = instance.bbox
    bbox_values = (x1, y1, x2, y2)
    if (
        all(math.isfinite(value) for value in bbox_values)
        and x2 > x1
        and y2 > y1
    ):
        return (x1 + x2) / 2.0, (y1 + y2) / 2.0

    return None
