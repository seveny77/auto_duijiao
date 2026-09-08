# -*- coding: utf-8 -*-
"""语义分割质检配置及其 JSON 持久化。"""

from dataclasses import dataclass, field
import json
import math
import os
from typing import Any

from backend.inspection_types import (
    InspectionRegionRule,
    InspectionSizeRule,
    inspection_to_dict,
)


@dataclass
class CircleDetectionConfig:
    """专用 YOLO Detect 找圆模型的配置。"""

    model_path: str = ""
    confidence_floor: float = 0.25
    # 候选检测框短边/长边的最低比例；0 表示不按长宽比过滤。
    min_box_aspect_ratio: float = 0.75
    expected_circle_count: int = 1


def default_inspection_size_rules() -> list[InspectionSizeRule]:
    """返回当前确认的 A/B/C/D 工艺默认规则。

    本函数每次都会构造新对象，避免不同 ``InspectionConfig`` 共享可变
    规则列表。这里仅描述工艺数据，实际尺寸测量与判定在后续步骤接入。
    """

    def rule(
        rule_id: str,
        region_id: str,
        region_name: str,
        inner_radius_um: float,
        outer_radius_um: float,
        defect_class: str,
        measurement: str,
        min_size_um: float,
        max_size_um: float | None,
        max_instance_count: int | None,
        *,
        min_inclusive: bool = True,
        max_inclusive: bool = True,
    ) -> InspectionSizeRule:
        return InspectionSizeRule(
            rule_id=rule_id,
            region_id=region_id,
            region_name=region_name,
            inner_radius_um=inner_radius_um,
            outer_radius_um=outer_radius_um,
            defect_class=defect_class,
            measurement=measurement,
            min_size_um=min_size_um,
            max_size_um=max_size_um,
            min_inclusive=min_inclusive,
            max_inclusive=max_inclusive,
            max_instance_count=max_instance_count,
        )

    return [
        # A：关键区，任何污点、划痕均不允许。
        rule("A-spot-any", "A", "关键区", 0.0, 25.0,
             "污点", "equivalent_diameter_um", 0.0, None, 0),
        rule("A-scratch-any", "A", "关键区", 0.0, 25.0,
             "划痕", "width_um", 0.0, None, 0),
        # B：覆层区。小污点及窄划痕不限数量，边界归属按工艺要求固定。
        rule("B-spot-small", "B", "覆层区", 25.0, 120.0,
             "污点", "equivalent_diameter_um", 0.0, 5.0, None,
             max_inclusive=False),
        rule("B-spot-medium", "B", "覆层区", 25.0, 120.0,
             "污点", "equivalent_diameter_um", 5.0, 10.0, 3),
        rule("B-spot-large", "B", "覆层区", 25.0, 120.0,
             "污点", "equivalent_diameter_um", 10.0, None, 0,
             min_inclusive=False),
        rule("B-scratch-narrow", "B", "覆层区", 25.0, 120.0,
             "划痕", "width_um", 0.0, 3.0, None),
        rule("B-scratch-wide", "B", "覆层区", 25.0, 120.0,
             "划痕", "width_um", 3.0, None, 0,
             min_inclusive=False),
        # C：粘合区，只显示与记录，不参与合格判定。
        rule("C-spot-any", "C", "粘合区", 120.0, 130.0,
             "污点", "equivalent_diameter_um", 0.0, None, None),
        rule("C-scratch-any", "C", "粘合区", 120.0, 130.0,
             "划痕", "width_um", 0.0, None, None),
        # D：接触区，划痕不控制；污点按 20 / 50 µm 分段。
        rule("D-spot-small", "D", "接触区", 130.0, 250.0,
             "污点", "equivalent_diameter_um", 0.0, 20.0, None,
             max_inclusive=False),
        rule("D-spot-medium", "D", "接触区", 130.0, 250.0,
             "污点", "equivalent_diameter_um", 20.0, 50.0, 3),
        rule("D-spot-large", "D", "接触区", 130.0, 250.0,
             "污点", "equivalent_diameter_um", 50.0, None, 0,
             min_inclusive=False),
        rule("D-scratch-any", "D", "接触区", 130.0, 250.0,
             "划痕", "width_um", 0.0, None, None),
    ]


@dataclass
class InspectionConfig:
    """最终成像语义分割质检的独立配置。"""

    enabled: bool = True
    model_path: str = ""
    inference_imgsz: int = 1024
    inference_confidence_floor: float = 0.01
    # 分割实例的检测框 NMS IoU 阈值；值越小，重叠候选去重越强。
    inference_nms_iou: float = 0.30
    mm_per_pixel: float = 0.0
    history_root: str = "inspection_history"
    # 正式自动对焦完成后异步追加到按日保存的 Excel；离线检测不记录。
    excel_record_enabled: bool = True
    excel_record_root: str = "inspection_records"
    circle: CircleDetectionConfig = field(
        default_factory=CircleDetectionConfig
    )
    region_rules: list[InspectionRegionRule] = field(default_factory=list)
    # v2 为“区域 × 缺陷类别 × 尺寸段”规则；旧 region_rules 仍保留，
    # 直到后续规则引擎切换完成，避免影响当前生产检测流程。
    rule_schema_version: int = 2
    size_rules: list[InspectionSizeRule] = field(
        default_factory=default_inspection_size_rules
    )
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
        if self.excel_record_enabled and not str(self.excel_record_root).strip():
            errors.append("Excel 检测记录目录不能为空")

        if self.inference_imgsz < 1:
            errors.append("分割推理尺寸 inference_imgsz 必须大于 0")
        if not math.isfinite(self.inference_confidence_floor) or not (
            0 <= self.inference_confidence_floor <= 1
        ):
            errors.append("分割推理置信度下限必须在 0～1 之间")
        if not math.isfinite(self.inference_nms_iou) or not (
            0 < self.inference_nms_iou <= 1
        ):
            errors.append("分割推理 NMS IoU 阈值必须在 (0, 1] 之间")

        roi_error = _roi_size_error(self.roi_size_px)
        if roi_error:
            errors.append(roi_error)

        errors.extend(_validate_circle_config(self.circle))
        errors.extend(_validate_region_rules(self.region_rules))
        errors.extend(_validate_size_rules(self.size_rules))
        return errors

    def validate_evaluation(self) -> list[str]:
        """只校验当前图规则复判直接依赖的比例和区域规则。"""

        errors = []
        if not math.isfinite(self.mm_per_pixel) or self.mm_per_pixel <= 0:
            errors.append("像素标定比例 mm_per_pixel 必须大于 0")
        errors.extend(_validate_region_rules(self.region_rules))
        errors.extend(_validate_size_rules(self.size_rules))
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
    size_rules_payload = payload.get("size_rules")
    roi_size_px = payload.get("roi_size_px", defaults.roi_size_px)
    roi_error = _roi_size_error(roi_size_px)
    if roi_error:
        raise ValueError(roi_error)

    if not isinstance(circle_payload, dict):
        raise ValueError("circle 必须是 JSON 对象")
    if not isinstance(rules_payload, list):
        raise ValueError("region_rules 必须是 JSON 数组")
    if size_rules_payload is not None and not isinstance(size_rules_payload, list):
        raise ValueError("size_rules 必须是 JSON 数组")

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
        min_box_aspect_ratio=float(circle_payload.get(
            "min_box_aspect_ratio", circle_defaults.min_box_aspect_ratio
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

    if size_rules_payload is None:
        # 旧配置尚未携带新版工艺规则时，使用已确认的默认工艺表。
        size_rules = default_inspection_size_rules()
    else:
        size_rules = []
        for index, item in enumerate(size_rules_payload):
            if not isinstance(item, dict):
                raise ValueError(f"size_rules[{index}] 必须是 JSON 对象")
            size_rules.append(_size_rule_from_dict(item))

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
        inference_nms_iou=float(payload.get(
            "inference_nms_iou", defaults.inference_nms_iou
        )),
        mm_per_pixel=float(payload.get(
            "mm_per_pixel", defaults.mm_per_pixel
        )),
        history_root=str(payload.get("history_root", defaults.history_root)),
        excel_record_enabled=bool(payload.get(
            "excel_record_enabled", defaults.excel_record_enabled
        )),
        excel_record_root=str(payload.get(
            "excel_record_root", defaults.excel_record_root
        )),
        circle=circle,
        region_rules=rules,
        rule_schema_version=int(payload.get(
            "rule_schema_version", defaults.rule_schema_version
        )),
        size_rules=size_rules,
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


def _size_rule_from_dict(payload: dict[str, Any]) -> InspectionSizeRule:
    """从一条新版尺寸规则恢复类型化数据。"""

    max_size_value = payload.get("max_size_um")
    max_count_value = payload.get("max_instance_count")
    return InspectionSizeRule(
        rule_id=str(payload.get("rule_id", "")),
        region_id=str(payload.get("region_id", "")),
        region_name=str(payload.get("region_name", "")),
        inner_radius_um=float(payload.get("inner_radius_um", 0.0)),
        outer_radius_um=float(payload.get("outer_radius_um", 0.0)),
        defect_class=str(payload.get("defect_class", "")),
        measurement=str(payload.get("measurement", "")),
        min_size_um=float(payload.get("min_size_um", 0.0)),
        max_size_um=(
            None if max_size_value is None else float(max_size_value)
        ),
        min_inclusive=bool(payload.get("min_inclusive", True)),
        max_inclusive=bool(payload.get("max_inclusive", True)),
        max_instance_count=(
            None if max_count_value is None else int(max_count_value)
        ),
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
    if not math.isfinite(config.min_box_aspect_ratio) or not (
        0 <= config.min_box_aspect_ratio <= 1
    ):
        errors.append("找圆候选框最小长宽比必须在 0～1 之间")
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
        if rule.class_id < -1:
            errors.append(f"{label} class_id 不能小于 -1")
        if rule.class_id == -1:
            if rule.class_name.strip() not in ("", "全部缺陷"):
                errors.append(
                    f"{label} 区域统一规则的 class_name 必须为空或为全部缺陷"
                )
        elif not rule.class_name.strip():
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


def _validate_size_rules(rules: list[InspectionSizeRule]) -> list[str]:
    """检查新版尺寸规则的字段、圆环定义与尺寸段连续性。"""

    errors: list[str] = []
    seen_rule_ids: set[str] = set()
    region_specs: dict[str, tuple[str, float, float]] = {}
    buckets: dict[tuple[str, str, str], list[InspectionSizeRule]] = {}
    allowed_measurement = {
        "污点": "equivalent_diameter_um",
        "划痕": "width_um",
    }

    for index, rule in enumerate(rules):
        label = f"尺寸规则[{index}]"
        if not rule.rule_id.strip():
            errors.append(f"{label} rule_id 不能为空")
        elif rule.rule_id in seen_rule_ids:
            errors.append(f"尺寸规则 rule_id 重复: {rule.rule_id}")
        seen_rule_ids.add(rule.rule_id)

        if not rule.region_id.strip():
            errors.append(f"{label} region_id 不能为空")
        if not rule.region_name.strip():
            errors.append(f"{label} region_name 不能为空")
        if not math.isfinite(rule.inner_radius_um) or rule.inner_radius_um < 0:
            errors.append(f"{label} 内半径必须是大于等于 0 的有效数字")
        if (not math.isfinite(rule.outer_radius_um)
                or rule.outer_radius_um <= rule.inner_radius_um):
            errors.append(f"{label} 外半径必须大于内半径")

        expected_measurement = allowed_measurement.get(rule.defect_class)
        if expected_measurement is None:
            errors.append(f"{label} 缺陷类别必须是污点或划痕")
        elif rule.measurement != expected_measurement:
            errors.append(
                f"{label} 的 {rule.defect_class} 测量方式必须为"
                f" {expected_measurement}"
            )

        if not math.isfinite(rule.min_size_um) or rule.min_size_um < 0:
            errors.append(f"{label} 尺寸下限必须是大于等于 0 的有效数字")
        if rule.max_size_um is not None:
            if (not math.isfinite(rule.max_size_um)
                    or rule.max_size_um < rule.min_size_um):
                errors.append(f"{label} 尺寸上限必须大于等于下限")
            elif (math.isclose(rule.max_size_um, rule.min_size_um)
                  and not (rule.min_inclusive and rule.max_inclusive)):
                errors.append(f"{label} 相同尺寸上下限必须同时包含边界")

        if rule.max_instance_count is not None:
            if (isinstance(rule.max_instance_count, bool)
                    or not isinstance(rule.max_instance_count, int)
                    or rule.max_instance_count < 0):
                errors.append(f"{label} 数量上限必须是非负整数或 null")

        spec = (
            rule.region_name,
            rule.inner_radius_um,
            rule.outer_radius_um,
        )
        previous_spec = region_specs.get(rule.region_id)
        if previous_spec is not None and previous_spec != spec:
            errors.append(f"尺寸规则区域 {rule.region_id} 的名称或半径定义不一致")
        else:
            region_specs[rule.region_id] = spec

        buckets.setdefault(
            (rule.region_id, rule.defect_class, rule.measurement), []
        ).append(rule)

    _validate_size_rule_regions(region_specs, errors)
    for key, bucket in buckets.items():
        _validate_size_rule_bucket(key, bucket, errors)

    return errors


def _validate_size_rule_regions(
    region_specs: dict[str, tuple[str, float, float]],
    errors: list[str],
) -> None:
    """新版规则的 A/B/C/D 圆环必须从 0 开始且首尾连续。"""

    ordered_regions = sorted(region_specs.items(), key=lambda item: item[1][1])
    if not ordered_regions:
        return

    first_id, first_spec = ordered_regions[0]
    if not math.isclose(first_spec[1], 0.0, abs_tol=1e-9):
        errors.append(f"首个尺寸规则圆环 {first_id} 的内半径必须为 0")

    for (previous_id, previous), (current_id, current) in zip(
            ordered_regions,
            ordered_regions[1:],
    ):
        if not math.isclose(previous[2], current[1], abs_tol=1e-9):
            errors.append(
                f"尺寸规则圆环 {previous_id} 与 {current_id} 之间存在空隙或重叠"
            )


def _validate_size_rule_bucket(
    key: tuple[str, str, str],
    rules: list[InspectionSizeRule],
    errors: list[str],
) -> None:
    """确保每个区域/类别的尺寸段从 0 连续覆盖到无上限。"""

    region_id, defect_class, _ = key
    ordered = sorted(rules, key=lambda rule: rule.min_size_um)
    if not ordered:
        return

    first = ordered[0]
    if not math.isclose(first.min_size_um, 0.0, abs_tol=1e-9):
        errors.append(
            f"尺寸规则 {region_id}/{defect_class} 的首个尺寸段必须从 0 开始"
        )
        return

    previous = first
    for current in ordered[1:]:
        if previous.max_size_um is None:
            errors.append(
                f"尺寸规则 {region_id}/{defect_class} 存在无上限段后的重复尺寸段"
            )
            break
        if not math.isclose(previous.max_size_um, current.min_size_um,
                              abs_tol=1e-9):
            errors.append(
                f"尺寸规则 {region_id}/{defect_class} 的尺寸段存在空隙或重叠"
            )
        elif previous.max_inclusive == current.min_inclusive:
            errors.append(
                f"尺寸规则 {region_id}/{defect_class} 在"
                f" {current.min_size_um:g} µm 边界存在空隙或重叠"
            )
        previous = current

    if previous.max_size_um is not None:
        errors.append(
            f"尺寸规则 {region_id}/{defect_class} 的最后一个尺寸段必须无上限"
        )
