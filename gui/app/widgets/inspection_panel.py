# -*- coding: utf-8 -*-
"""语义分割检测结果页。

本模块负责界面布局、检测配置收集和结果展示，不直接加载模型，
也不访问相机、运动控制器或对焦流程。
"""

import copy
from datetime import datetime
from pathlib import Path

from PyQt5.QtCore import QTimer, Qt, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMenu,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from backend.inspection_config import InspectionConfig
from backend.inspection_renderer import (
    render_image_inspection_overlay,
    render_inspection_overlay,
)
from backend.inspection_types import (
    ImageInspectionResult,
    InspectionRegionRule,
)
from gui.app.widgets.image_view import ImageWidget, ZoomableGraphicsView


class InspectionPanel(QWidget):
    """检测结果工作区的第一版可视化框架。"""

    model_load_requested = pyqtSignal(str)
    offline_image_test_requested = pyqtSignal(str, object)
    inspection_config_save_requested = pyqtSignal(object)
    inspection_config_invalid = pyqtSignal(str)
    inspection_recalculate_requested = pyqtSignal(object)
    circle_redetection_requested = pyqtSignal(object)
    circle_confirmation_requested = pyqtSignal(object)
    original_image_saved = pyqtSignal(str)
    original_image_save_failed = pyqtSignal(str)
    focus_start_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._original_image = None
        self._inspection_result = None
        self._image_inspection_result = None
        self._selected_circle_result = None
        self._inspection_config = None
        self._base_inspection_config = InspectionConfig()
        self._model_class_names = {0: "异物", 1: "脏污"}
        self._updating_config_ui = False
        self._last_offline_image_path = ""
        self._circle_operation_busy = False
        self._recalculate_timer = QTimer(self)
        self._recalculate_timer.setSingleShot(True)
        self._recalculate_timer.setInterval(250)
        self._recalculate_timer.timeout.connect(
            self._emit_recalculation_request
        )
        self._build_ui()
        self._apply_preview_state()
        self._apply_style()

        self.select_model_btn.clicked.connect(self._choose_model)
        self.select_circle_model_btn.clicked.connect(self._choose_circle_model)
        self.load_model_btn.clicked.connect(self._request_model_load)
        self.start_focus_btn.clicked.connect(self.focus_start_requested.emit)
        self.select_local_image_btn.clicked.connect(self._choose_offline_image)
        self.display_mode_combo.currentIndexChanged.connect(
            self._refresh_result_image
        )
        self.show_masks_check.toggled.connect(self._refresh_result_image)
        self.show_circle_check.toggled.connect(self._refresh_result_image)
        self.show_rings_check.toggled.connect(self._refresh_result_image)
        self.fit_image_btn.clicked.connect(self._fit_result_image)
        self.reset_view_btn.clicked.connect(self._show_result_one_to_one)
        self.add_region_btn.clicked.connect(self._add_region)
        self.remove_region_btn.clicked.connect(self._remove_region)
        self.save_config_btn.clicked.connect(self._request_config_save)
        self.find_circle_btn.clicked.connect(
            self._request_circle_redetection
        )
        self.confirm_circle_btn.clicked.connect(
            self._request_circle_confirmation
        )
        self.mm_per_pixel_spin.valueChanged.connect(
            self._schedule_recalculation
        )
        self.region_table.itemChanged.connect(self._schedule_recalculation)
        self.rule_table.itemChanged.connect(self._schedule_recalculation)
        self.circle_result_table.itemSelectionChanged.connect(
            self._on_circle_result_selection_changed
        )

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("缺陷检测")
        title.setObjectName("inspectionTitle")
        subtitle = QLabel("最终图分割、找圆与分区判定")
        subtitle.setObjectName("inspectionSubtitle")
        self.state_badge = QLabel("未加载模型")
        self.state_badge.setObjectName("inspectionStateBadge")
        self.state_badge.setAlignment(Qt.AlignCenter)
        header.addWidget(title)
        header.addWidget(subtitle)
        header.addStretch(1)
        self.start_focus_btn = QPushButton("开始执行对焦")
        self.start_focus_btn.setToolTip(
            "使用“对焦过程”页中当前配置启动一次连续精扫对焦"
        )
        header.addWidget(self.start_focus_btn)
        header.addWidget(self.state_badge)
        root.addLayout(header)

        self.workspace_splitter = QSplitter(Qt.Horizontal)
        self.workspace_splitter.setChildrenCollapsible(False)
        self.workspace_splitter.addWidget(self._build_result_workspace())
        self.workspace_splitter.addWidget(self._build_side_workspace())
        self.workspace_splitter.setStretchFactor(0, 1)
        self.workspace_splitter.setStretchFactor(1, 0)
        self.workspace_splitter.setSizes([780, 440])
        root.addWidget(self.workspace_splitter, 1)

    def _build_result_workspace(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        image_group = QGroupBox("检测图像")
        image_layout = QVBoxLayout(image_group)

        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("显示："))
        self.display_mode_combo = QComboBox()
        self.display_mode_combo.addItems([
            "检测结果图",
            "原始最终图",
            "仅缺陷轮廓",
        ])
        toolbar.addWidget(self.display_mode_combo)
        self.show_masks_check = QCheckBox("缺陷轮廓")
        self.show_masks_check.setChecked(True)
        self.show_circle_check = QCheckBox("检测圆")
        self.show_circle_check.setChecked(True)
        self.show_rings_check = QCheckBox("质检圆环")
        self.show_rings_check.setChecked(True)
        toolbar.addWidget(self.show_masks_check)
        toolbar.addWidget(self.show_circle_check)
        toolbar.addWidget(self.show_rings_check)
        self.select_local_image_btn = QPushButton("选择本地图片…")
        toolbar.addWidget(self.select_local_image_btn)
        toolbar.addStretch(1)
        self.fit_image_btn = QPushButton("适应窗口")
        self.reset_view_btn = QPushButton("1:1")
        toolbar.addWidget(self.fit_image_btn)
        toolbar.addWidget(self.reset_view_btn)
        image_layout.addLayout(toolbar)

        self.image_frame = QFrame()
        self.image_frame.setObjectName("inspectionImageFrame")
        self.image_frame.setMinimumHeight(330)
        frame_layout = QVBoxLayout(self.image_frame)
        frame_layout.setContentsMargins(0, 0, 0, 0)
        self.image_placeholder = QLabel(
            "尚无检测图像\n\n"
            "完成对焦并取得最终成像后，\n"
            "缺陷轮廓、检测圆和质检圆环将在这里叠加显示。"
        )
        self.image_placeholder.setObjectName("inspectionImagePlaceholder")
        self.image_placeholder.setAlignment(Qt.AlignCenter)
        frame_layout.addWidget(self.image_placeholder)

        self._image_scene = QGraphicsScene(self)
        self._image_item = QGraphicsPixmapItem()
        self._image_scene.addItem(self._image_item)
        self._image_view = ZoomableGraphicsView(self._image_scene)
        self._image_view.setAlignment(Qt.AlignCenter)
        self._image_view.setStyleSheet("background-color: #171a20;")
        self._image_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self._image_view.customContextMenuRequested.connect(
            self._show_original_image_context_menu
        )
        self._image_view.hide()
        frame_layout.addWidget(self._image_view)
        image_layout.addWidget(self.image_frame, 1)
        layout.addWidget(image_group, 1)
        return container

    def _build_summary_page(self) -> QWidget:
        page = QWidget()
        layout = QHBoxLayout(page)

        verdict_box = QFrame()
        verdict_box.setObjectName("inspectionVerdictBox")
        verdict_layout = QVBoxLayout(verdict_box)
        verdict_caption = QLabel("整图判定")
        verdict_caption.setAlignment(Qt.AlignCenter)
        self.verdict_label = QLabel("待检测")
        self.verdict_label.setObjectName("inspectionVerdict")
        self.verdict_label.setAlignment(Qt.AlignCenter)
        self.verdict_detail_label = QLabel("尚未收到最终成像")
        self.verdict_detail_label.setAlignment(Qt.AlignCenter)
        self.verdict_detail_label.setWordWrap(True)
        verdict_layout.addStretch(1)
        verdict_layout.addWidget(verdict_caption)
        verdict_layout.addWidget(self.verdict_label)
        verdict_layout.addWidget(self.verdict_detail_label)
        verdict_layout.addStretch(1)
        layout.addWidget(verdict_box, 1)

        metrics_group = QGroupBox("本次检测")
        metrics_form = QFormLayout(metrics_group)
        self.instance_count_label = QLabel("--")
        self.circle_result_label = QLabel("--")
        self.image_size_label = QLabel("--")
        self.inference_time_label = QLabel("--")
        self.total_time_label = QLabel("--")
        metrics_form.addRow("缺陷实例总数：", self.instance_count_label)
        metrics_form.addRow("找圆结果：", self.circle_result_label)
        metrics_form.addRow("原图尺寸：", self.image_size_label)
        metrics_form.addRow("模型推理：", self.inference_time_label)
        metrics_form.addRow("检测总耗时：", self.total_time_label)
        layout.addWidget(metrics_group, 1)

        return page

    def _build_detail_page(self) -> QWidget:
        """把判定摘要和规则表合并到右侧的一个选项卡中。"""

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(8)
        layout.addWidget(self._build_summary_page())
        layout.addWidget(self._build_circle_result_group())
        layout.addWidget(self._build_rule_page(), 1)
        return page

    def _build_circle_result_group(self) -> QGroupBox:
        group = QGroupBox("端面结果")
        layout = QVBoxLayout(group)
        self.circle_result_table = QTableWidget(0, 5)
        self.circle_result_table.setHorizontalHeaderLabels([
            "端面",
            "状态",
            "圆评分",
            "ROI",
            "缺陷数",
        ])
        self.circle_result_table.verticalHeader().setVisible(False)
        self.circle_result_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )
        self.circle_result_table.setSelectionBehavior(
            QAbstractItemView.SelectRows
        )
        self.circle_result_table.setSelectionMode(
            QAbstractItemView.SingleSelection
        )
        self.circle_result_table.setEditTriggers(
            QAbstractItemView.NoEditTriggers
        )
        self.circle_result_table.setMinimumHeight(120)
        layout.addWidget(self.circle_result_table)

        self.circle_detail_label = QLabel("请选择端面查看详细结果")
        self.circle_detail_label.setWordWrap(True)
        layout.addWidget(self.circle_detail_label)
        return group

    def _build_rule_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        hint = QLabel(
            "规则按“圆环区域 × 缺陷类别”配置；点击上方端面行"
            "查看该端面的统计结果。"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.rule_table = QTableWidget(4, 7)
        self.rule_table.setHorizontalHeaderLabels([
            "区域",
            "缺陷类别",
            "最低置信度",
            "最小面积 um²",
            "数量上限",
            "当前数量",
            "结论",
        ])
        preview_rows = [
            ("中心区 0–R1", "异物", "0.25", "0.010", "0"),
            ("中心区 0–R1", "脏污", "0.25", "0.050", "0"),
            ("外环区 R1–R2", "异物", "0.25", "0.010", "0"),
            ("外环区 R1–R2", "脏污", "0.25", "0.050", "0"),
        ]
        for row, values in enumerate(preview_rows):
            for column, text in enumerate(values):
                self.rule_table.setItem(row, column, QTableWidgetItem(text))
            self.rule_table.setItem(row, 5, QTableWidgetItem("--"))
            self.rule_table.setItem(row, 6, QTableWidgetItem("待检测"))
        self.rule_table.verticalHeader().setVisible(False)
        self.rule_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )
        self.rule_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        layout.addWidget(self.rule_table)
        return page

    def _build_side_workspace(self) -> QWidget:
        self.side_tabs = QTabWidget()
        self.side_tabs.setMinimumWidth(420)
        self.side_tabs.addTab(self._build_settings_page(), "检测设置")
        self.side_tabs.addTab(self._build_detail_page(), "判定与规则")
        self.side_tabs.addTab(self._build_history_page(), "历史记录")
        return self.side_tabs

    def _build_settings_page(self) -> QWidget:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(8)
        layout.addWidget(self._build_model_group())
        layout.addWidget(self._build_circle_group())
        layout.addWidget(self._build_region_group())
        self.save_config_btn = QPushButton("保存检测配置")
        self.save_config_btn.setMinimumHeight(32)
        layout.addWidget(self.save_config_btn)
        layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(content)
        return scroll

    def _build_model_group(self) -> QGroupBox:
        group = QGroupBox("1. 检测模型")
        layout = QVBoxLayout(group)
        layout.addWidget(QLabel("缺陷分割模型（YOLO-Seg）："))
        self.model_path_edit = QLineEdit()
        self.model_path_edit.setReadOnly(True)
        self.model_path_edit.setPlaceholderText("请选择 YOLO-Seg best.pt")
        layout.addWidget(self.model_path_edit)

        buttons = QHBoxLayout()
        self.select_model_btn = QPushButton("选择分割模型…")
        self.load_model_btn = QPushButton("加载并预热模型")
        buttons.addWidget(self.select_model_btn)
        buttons.addWidget(self.load_model_btn)
        layout.addLayout(buttons)

        layout.addWidget(QLabel("端面找圆模型（YOLO Detect，最长边固定 1024）："))
        self.circle_model_path_edit = QLineEdit()
        self.circle_model_path_edit.setReadOnly(True)
        self.circle_model_path_edit.setPlaceholderText("请选择普通 YOLO Detect best.pt")
        layout.addWidget(self.circle_model_path_edit)
        self.select_circle_model_btn = QPushButton("选择找圆模型…")
        layout.addWidget(self.select_circle_model_btn)

        form = QFormLayout()
        self.model_status_label = QLabel("尚未加载")
        self.model_classes_label = QLabel("--")
        self.model_load_time_label = QLabel("--")
        self.model_warmup_time_label = QLabel("--")
        self.circle_model_time_label = QLabel("--")
        form.addRow("状态：", self.model_status_label)
        form.addRow("类别：", self.model_classes_label)
        form.addRow("加载耗时：", self.model_load_time_label)
        form.addRow("预热耗时：", self.model_warmup_time_label)
        form.addRow("找圆模型加载/预热：", self.circle_model_time_label)
        layout.addLayout(form)
        return group

    @property
    def selected_model_path(self) -> str:
        """返回当前页面中选择的模型路径。"""

        return self.model_path_edit.text().strip()

    @property
    def selected_circle_model_path(self) -> str:
        return self.circle_model_path_edit.text().strip()

    def set_model_path(self, path: str):
        """显示模型路径，并根据路径是否为空更新加载按钮。"""

        self.model_path_edit.setText(str(path or ""))
        self.load_model_btn.setEnabled(
            bool(str(path or "").strip())
            and bool(self.selected_circle_model_path)
        )

    def set_circle_model_path(self, path: str):
        """显示专用找圆模型路径。"""

        self.circle_model_path_edit.setText(str(path or ""))
        self.load_model_btn.setEnabled(
            bool(self.selected_model_path)
            and bool(str(path or "").strip())
        )

    def set_inspection_config(self, config: InspectionConfig):
        """把检测配置完整映射到界面，但不触发检测或找圆。"""

        self._updating_config_ui = True
        try:
            self._base_inspection_config = copy.deepcopy(config)
            self.set_model_path(config.model_path)
            self.set_circle_model_path(config.circle.model_path)
            self.mm_per_pixel_spin.setValue(float(config.mm_per_pixel))
            self.circle_confidence_spin.setValue(
                float(config.circle.confidence_floor)
            )
            self.expected_circle_count_spin.setValue(
                int(config.circle.expected_circle_count)
            )

            rules = list(config.region_rules or [])
            if rules:
                self._model_class_names = {
                    int(rule.class_id): str(rule.class_name)
                    for rule in rules
                }

            regions = {}
            for rule in rules:
                regions.setdefault(
                    rule.region_id,
                    (
                        rule.region_name,
                        float(rule.inner_radius_mm),
                        float(rule.outer_radius_mm),
                    ),
                )
            if not regions:
                regions = {
                    "region_1": ("中心区", 0.0, 10.0),
                    "region_2": ("外环区", 10.0, 20.0),
                }

            ordered_regions = sorted(
                regions.items(), key=lambda item: item[1][1]
            )
            self.region_table.setRowCount(len(ordered_regions))
            for row, (region_id, values) in enumerate(ordered_regions):
                name, inner_radius, outer_radius = values
                name_item = QTableWidgetItem(str(name))
                name_item.setData(Qt.UserRole, str(region_id))
                self.region_table.setItem(row, 0, name_item)
                self.region_table.setItem(
                    row, 1, QTableWidgetItem(f"{inner_radius:g}")
                )
                self.region_table.setItem(
                    row, 2, QTableWidgetItem(f"{outer_radius:g}")
                )

            self._rebuild_rule_rows(preferred_rules=rules)
        finally:
            self._updating_config_ui = False

    def build_inspection_config(self) -> InspectionConfig:
        """读取界面值，构造新配置并保留界面尚未开放的参数。"""

        config = copy.deepcopy(self._base_inspection_config)
        config.model_path = self.selected_model_path
        config.circle.model_path = self.selected_circle_model_path
        config.mm_per_pixel = float(self.mm_per_pixel_spin.value())
        config.circle.confidence_floor = float(
            self.circle_confidence_spin.value()
        )
        config.circle.expected_circle_count = int(
            self.expected_circle_count_spin.value()
        )

        regions = self._read_region_rows()
        rule_values = self._current_rule_values()
        rules = []
        for region_id, region_name, inner_radius, outer_radius in regions:
            for class_id, class_name in sorted(self._model_class_names.items()):
                values = rule_values.get((region_id, class_id), (0.25, 0.0, 0))
                rules.append(InspectionRegionRule(
                    region_id=region_id,
                    region_name=region_name,
                    inner_radius_mm=inner_radius,
                    outer_radius_mm=outer_radius,
                    class_id=class_id,
                    class_name=class_name,
                    min_confidence=values[0],
                    min_instance_area_mm2=values[1],
                    max_instance_count=values[2],
                ))
        config.region_rules = rules
        return config

    def accept_inspection_config(self, config: InspectionConfig):
        """记录最近一次保存成功的配置，供后续编辑时保留隐藏字段。"""

        self._updating_config_ui = True
        try:
            self._base_inspection_config = copy.deepcopy(config)
            if self._inspection_result is None:
                self._rebuild_rule_rows(preferred_rules=config.region_rules)
            else:
                self._update_rule_results(self._inspection_result, config)
        finally:
            self._updating_config_ui = False

    def set_model_loading(self, path: str):
        """显示后台加载状态。"""

        self.set_model_path(path)
        self.model_status_label.setText("正在加载…")
        self.state_badge.setText("加载中")
        self.select_model_btn.setEnabled(False)
        self.select_circle_model_btn.setEnabled(False)
        self.load_model_btn.setEnabled(False)

    def set_model_loaded(self, path: str, metadata):
        """显示模型加载成功及类别、耗时信息。"""

        self.set_model_path(path)
        self.model_status_label.setText("已加载")
        names = metadata.get("class_names", {}) if isinstance(metadata, dict) else {}
        normalized_names = _normalize_class_names(names)
        if normalized_names:
            self._model_class_names = normalized_names
            self._rebuild_rule_rows()
        self.model_classes_label.setText(
            "、".join(
                f"{key}: {value}"
                for key, value in self._model_class_names.items()
            )
            or "--"
        )
        self.model_load_time_label.setText(
            _format_ms(metadata.get("load_ms")) if isinstance(metadata, dict) else "--"
        )
        self.model_warmup_time_label.setText(
            _format_ms(metadata.get("warmup_ms")) if isinstance(metadata, dict) else "--"
        )
        self.circle_model_time_label.setText(
            f"{_format_ms(metadata.get('circle_load_ms'))} / "
            f"{_format_ms(metadata.get('circle_warmup_ms'))}"
            if isinstance(metadata, dict) else "--"
        )
        self.state_badge.setText("模型已加载")
        self.select_model_btn.setEnabled(False)
        self.select_circle_model_btn.setEnabled(False)
        self.load_model_btn.setEnabled(False)

    def set_model_load_failed(self, message: str):
        """显示模型加载失败，并允许用户重新选择。"""

        self.model_status_label.setText("加载失败")
        self.model_classes_label.setText("--")
        self.model_load_time_label.setText("--")
        self.model_warmup_time_label.setText("--")
        self.circle_model_time_label.setText("--")
        self.state_badge.setText("加载失败")
        self.select_model_btn.setEnabled(True)
        self.select_circle_model_btn.setEnabled(True)
        self.load_model_btn.setEnabled(
            bool(self.selected_model_path) and bool(self.selected_circle_model_path)
        )
        self.model_status_label.setToolTip(str(message))

    def _choose_model(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 YOLO-Seg 模型",
            self.selected_model_path,
            "PyTorch 模型 (*.pt);;所有文件 (*.*)",
        )
        if path:
            self.set_model_path(path)

    def _choose_circle_model(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择端面找圆 YOLO Detect 模型",
            self.selected_circle_model_path,
            "PyTorch 模型 (*.pt);;所有文件 (*.*)",
        )
        if path:
            self.set_circle_model_path(path)

    def _choose_offline_image(self):
        """选择一张本地图，并将路径和页面当前配置交给主窗口。"""

        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择本地检测图片",
            self._last_offline_image_path,
            (
                "图像文件 (*.jpg *.jpeg *.png *.bmp *.tif *.tiff);;"
                "所有文件 (*.*)"
            ),
        )
        if not path:
            return

        try:
            config = self.build_inspection_config()
        except ValueError as error:
            self.inspection_config_invalid.emit(str(error))
            return
        errors = config.validate()
        if errors:
            self.inspection_config_invalid.emit(errors[0])
            return

        self._last_offline_image_path = path
        self.offline_image_test_requested.emit(path, config)

    def _request_model_load(self):
        path = self.selected_model_path
        if path and self.selected_circle_model_path:
            self.model_load_requested.emit(path)

    def _read_region_rows(self):
        """读取圆环表；错误直接指出具体行，交给保存入口显示。"""

        regions = []
        for row in range(self.region_table.rowCount()):
            name_item = self.region_table.item(row, 0)
            region_name = name_item.text().strip() if name_item else ""
            if not region_name:
                raise ValueError(f"第 {row + 1} 个圆环的区域名称不能为空")

            region_id = name_item.data(Qt.UserRole) if name_item else None
            region_id = str(region_id or f"region_{row + 1}")
            try:
                inner_radius = float(self.region_table.item(row, 1).text())
                outer_radius = float(self.region_table.item(row, 2).text())
            except (AttributeError, TypeError, ValueError):
                raise ValueError(
                    f"第 {row + 1} 个圆环的内、外半径必须是数字"
                ) from None
            regions.append(
                (region_id, region_name, inner_radius, outer_radius)
            )
        return regions

    def _current_rule_values(self):
        """读取规则表中的三个可编辑阈值。"""

        values = {}
        for row in range(self.rule_table.rowCount()):
            region_item = self.rule_table.item(row, 0)
            class_item = self.rule_table.item(row, 1)
            region_id = region_item.data(Qt.UserRole) if region_item else None
            class_id = class_item.data(Qt.UserRole) if class_item else None
            if region_id is None or class_id is None:
                continue
            try:
                min_confidence = float(self.rule_table.item(row, 2).text())
                min_area = float(self.rule_table.item(row, 3).text())
                max_count = int(self.rule_table.item(row, 4).text())
            except (AttributeError, TypeError, ValueError):
                raise ValueError(
                    f"规则表第 {row + 1} 行的阈值必须是有效数字，"
                    "数量上限必须是整数"
                ) from None
            values[(str(region_id), int(class_id))] = (
                min_confidence,
                min_area,
                max_count,
            )
        return values

    def _rebuild_rule_rows(self, preferred_rules=None):
        """按当前圆环和模型类别重建“区域 × 类别”规则表。"""

        try:
            current_values = self._current_rule_values()
            regions = self._read_region_rows()
        except ValueError:
            current_values = {}
            regions = []

        if preferred_rules is not None:
            for rule in preferred_rules:
                current_values[(rule.region_id, int(rule.class_id))] = (
                    float(rule.min_confidence),
                    float(rule.min_instance_area_mm2),
                    int(rule.max_instance_count),
                )

        rules = []
        for region_id, region_name, inner_radius, outer_radius in regions:
            for class_id, class_name in sorted(self._model_class_names.items()):
                thresholds = current_values.get(
                    (region_id, class_id),
                    (0.25, 0.0, 0),
                )
                rules.append(InspectionRegionRule(
                    region_id=region_id,
                    region_name=region_name,
                    inner_radius_mm=inner_radius,
                    outer_radius_mm=outer_radius,
                    class_id=class_id,
                    class_name=class_name,
                    min_confidence=thresholds[0],
                    min_instance_area_mm2=thresholds[1],
                    max_instance_count=thresholds[2],
                ))
        self._populate_rule_table(rules)

    def _populate_rule_table(self, rules, results=None):
        """显示规则和可选统计结果，同时保留配置字段的可编辑性。"""

        results = results or {}
        previous_updating = self._updating_config_ui
        self._updating_config_ui = True
        try:
            self.rule_table.setRowCount(len(rules))
            for row, rule in enumerate(rules):
                current = results.get((rule.region_id, rule.class_id))
                region_text = (
                    f"{rule.region_name} "
                    f"{rule.inner_radius_mm:g}–{rule.outer_radius_mm:g}mm"
                )
                region_item = _readonly_item(region_text)
                region_item.setData(Qt.UserRole, str(rule.region_id))
                class_item = _readonly_item(str(rule.class_name))
                class_item.setData(Qt.UserRole, int(rule.class_id))
                self.rule_table.setItem(row, 0, region_item)
                self.rule_table.setItem(row, 1, class_item)
                self.rule_table.setItem(
                    row, 2, QTableWidgetItem(f"{rule.min_confidence:g}")
                )
                self.rule_table.setItem(
                    row, 3, QTableWidgetItem(
                        f"{rule.min_instance_area_mm2:g}"
                    )
                )
                self.rule_table.setItem(
                    row, 4, QTableWidgetItem(str(rule.max_instance_count))
                )
                self.rule_table.setItem(
                    row,
                    5,
                    _readonly_item(
                        str(current.valid_instance_count)
                        if current is not None else "--"
                    ),
                )
                verdict = (
                    "合格" if current is not None and current.passed
                    else "不合格" if current is not None
                    else "待检测"
                )
                self.rule_table.setItem(row, 6, _readonly_item(verdict))
        finally:
            self._updating_config_ui = previous_updating

    def _add_region(self):
        """在圆环表末尾添加一个默认宽度 10 mm 的连续圆环。"""

        row = self.region_table.rowCount()
        last_outer = 0.0
        if row:
            try:
                last_outer = float(self.region_table.item(row - 1, 2).text())
            except (AttributeError, TypeError, ValueError):
                self.inspection_config_invalid.emit(
                    "请先修正最后一个圆环的外半径，再新增圆环"
                )
                return

        existing_ids = {
            str(self.region_table.item(index, 0).data(Qt.UserRole))
            for index in range(row)
            if self.region_table.item(index, 0) is not None
        }
        number = row + 1
        while f"region_{number}" in existing_ids:
            number += 1
        self.region_table.insertRow(row)
        name_item = QTableWidgetItem(f"圆环区 {number}")
        name_item.setData(Qt.UserRole, f"region_{number}")
        self.region_table.setItem(row, 0, name_item)
        self.region_table.setItem(row, 1, QTableWidgetItem(f"{last_outer:g}"))
        self.region_table.setItem(
            row, 2, QTableWidgetItem(f"{last_outer + 10.0:g}")
        )
        self._rebuild_rule_rows()
        self._schedule_recalculation()

    def _remove_region(self):
        """删除选中圆环；没有选中时删除最后一个圆环。"""

        if self.region_table.rowCount() <= 1:
            self.inspection_config_invalid.emit("至少需要保留一个质检圆环")
            return
        row = self.region_table.currentRow()
        if row < 0:
            row = self.region_table.rowCount() - 1
        removed_inner = self.region_table.item(row, 1).text()
        self.region_table.removeRow(row)
        if row < self.region_table.rowCount():
            # 后一个圆环向内补齐被删除区域，继续保持首尾连续。
            self.region_table.item(row, 1).setText(removed_inner)
        self._rebuild_rule_rows()
        self._schedule_recalculation()

    def _request_config_save(self):
        """仅收集并发出配置；磁盘保存由主窗口统一负责。"""

        try:
            config = self.build_inspection_config()
        except ValueError as error:
            self.inspection_config_invalid.emit(str(error))
            return
        self.inspection_config_save_requested.emit(config)

    def _request_circle_redetection(self):
        """收集当前参数，请求重新找圆并按新 ROI 重跑分割。"""

        try:
            config = self.build_inspection_config()
        except ValueError as error:
            self.inspection_config_invalid.emit(str(error))
            return
        errors = config.validate()
        if errors:
            self.inspection_config_invalid.emit(errors[0])
            return
        self.circle_redetection_requested.emit(config)

    def _request_circle_confirmation(self):
        """请求确认评分最高的当前候选圆，不重新执行轮廓找圆。"""

        try:
            config = self.build_inspection_config()
        except ValueError as error:
            self.inspection_config_invalid.emit(str(error))
            return
        errors = config.validate_evaluation()
        if errors:
            self.inspection_config_invalid.emit(errors[0])
            return
        self.circle_confirmation_requested.emit(config)

    def set_circle_redetecting(self):
        """显示后台重新找圆状态并防止重复提交。"""

        self._circle_operation_busy = True
        self.find_circle_btn.setText("正在找圆…")
        self.find_circle_btn.setEnabled(False)
        self.confirm_circle_btn.setEnabled(False)
        self.state_badge.setText("正在找圆")

    def set_circle_redetection_failed(self, message: str):
        """恢复找圆控件，并保留当前图之前的检测结果。"""

        self._circle_operation_busy = False
        self.find_circle_btn.setText("重新找圆")
        self.state_badge.setText("找圆失败")
        self.state_badge.setToolTip(str(message))
        self._update_circle_controls(self._inspection_result)

    def _schedule_recalculation(self, _value=None):
        """先保存当前表格值，再合并连续编辑请求。"""

        if self._updating_config_ui:
            return

        # 规则表编辑后立即更新面板内存配置。否则切换多端面时，
        # _update_rule_results() 会按旧配置重建表格，导致刚输入的阈值丢失。
        # 这里的规则仍然只有一份，按区域×类别作用于所有端面。
        if not self._sync_rule_config_from_table():
            return

        if (
            self._inspection_result is None
            and self._image_inspection_result is None
        ):
            return
        self._recalculate_timer.start()

    def _emit_recalculation_request(self):
        """界面值完整且通过校验时，提交当前图轻量复判请求。"""

        if (
            self._inspection_result is None
            and self._image_inspection_result is None
        ):
            return
        try:
            config = self.build_inspection_config()
        except ValueError:
            return
        if config.validate_evaluation():
            return
        self.inspection_recalculate_requested.emit(config)

    def present_inspection_result(
        self,
        task_id: str,
        original_image,
        result,
        config,
    ):
        """保存本次原图和结果，刷新图像、摘要及区域规则表。"""

        self._original_image = original_image
        self._inspection_result = result
        self._image_inspection_result = None
        self._selected_circle_result = None
        self._inspection_config = config
        self._clear_circle_result_table()
        self.image_placeholder.hide()
        self._image_view.show()
        self.fit_image_btn.setEnabled(True)
        self.reset_view_btn.setEnabled(True)
        self._update_result_summary(task_id, result)
        self._update_rule_results(result, config)
        self._update_circle_controls(result)
        self._refresh_result_image(reset_view=True)

    def present_image_inspection_result(
        self,
        task_id: str,
        original_image,
        result: ImageInspectionResult,
        config,
    ):
        """展示整图多端面结果；不自动选择端面。"""

        if not isinstance(result, ImageInspectionResult):
            raise TypeError("result 必须是 ImageInspectionResult")
        # 结果可能在配置页尚未初始化时先到达；先用结果携带的区域定义
        # 对齐规则表，避免后续端面切换按默认 region_1/region_2 重建。
        config_region_ids = {
            str(rule.region_id) for rule in getattr(config, "region_rules", [])
        }
        table_region_ids = {
            str(self.region_table.item(row, 0).data(Qt.UserRole))
            for row in range(self.region_table.rowCount())
            if self.region_table.item(row, 0) is not None
        }
        if config_region_ids and config_region_ids != table_region_ids:
            self.set_inspection_config(config)
        self._original_image = original_image
        self._inspection_result = None
        self._image_inspection_result = result
        self._selected_circle_result = None
        self._inspection_config = config
        self._base_inspection_config = copy.deepcopy(config)
        if not self._model_class_names:
            self._model_class_names = {
                int(rule.class_id): str(rule.class_name)
                for rule in getattr(config, "region_rules", [])
            }
        self.image_placeholder.hide()
        self._image_view.show()
        self.fit_image_btn.setEnabled(True)
        self.reset_view_btn.setEnabled(True)
        self._update_image_result_summary(task_id, result)
        self._populate_circle_result_table(result)
        self._update_rule_results(None, config)
        self._update_image_circle_controls(result)
        self._refresh_result_image(reset_view=True)

    def present_image_recalculated_result(
        self,
        task_id: str,
        result: ImageInspectionResult,
        config,
    ):
        """刷新重新找圆后的多端面结果，并清除原端面选择。"""

        if self._original_image is None:
            return
        if not isinstance(result, ImageInspectionResult):
            raise TypeError("result 必须是 ImageInspectionResult")
        self._inspection_result = None
        self._image_inspection_result = result
        self._selected_circle_result = None
        self._inspection_config = config
        self._update_image_result_summary(task_id, result)
        self._populate_circle_result_table(result)
        self._update_rule_results(None, config)
        self._update_image_circle_controls(result)
        self._refresh_result_image(reset_view=False)

    def present_recalculated_result(self, task_id: str, result, config):
        """刷新当前图的复判结果，不替换原图，也不重置缩放位置。"""

        if self._original_image is None:
            return
        self._inspection_result = result
        self._image_inspection_result = None
        self._selected_circle_result = None
        self._inspection_config = config
        self._clear_circle_result_table()
        self._update_result_summary(task_id, result)
        self._update_rule_results(result, config)
        self._update_circle_controls(result)
        self._refresh_result_image(reset_view=False)

    def _populate_circle_result_table(self, result: ImageInspectionResult):
        """填充端面总览并显式保持无默认选择。"""

        circle_results = list(result.circle_results or [])
        self.circle_result_table.blockSignals(True)
        try:
            self.circle_result_table.clearContents()
            self.circle_result_table.setRowCount(len(circle_results))
            for row, circle_result in enumerate(circle_results):
                candidate = circle_result.circle_candidate
                roi = circle_result.roi
                status_text, status_color = _status_display(
                    circle_result.status
                )
                values = (
                    circle_result.circle_id or f"circle-{row + 1:03d}",
                    status_text,
                    (
                        f"{float(candidate.score):.3f}"
                        if candidate is not None else "--"
                    ),
                    (
                        f"{int(roi.width)}×{int(roi.height)}"
                        if roi is not None else "--"
                    ),
                    (
                        str(len(circle_result.instances))
                        if circle_result.completed else "--"
                    ),
                )
                for column, value in enumerate(values):
                    item = _readonly_item(value)
                    if column == 0:
                        item.setData(Qt.UserRole, row)
                    if column == 1:
                        item.setForeground(QColor(status_color))
                    self.circle_result_table.setItem(row, column, item)
            self.circle_result_table.clearSelection()
            self.circle_result_table.setCurrentCell(-1, -1)
        finally:
            self.circle_result_table.blockSignals(False)
        self.circle_detail_label.setText("请选择端面查看详细结果")

    def _clear_circle_result_table(self):
        self.circle_result_table.blockSignals(True)
        try:
            self.circle_result_table.clearContents()
            self.circle_result_table.setRowCount(0)
            self.circle_result_table.clearSelection()
            self.circle_result_table.setCurrentCell(-1, -1)
        finally:
            self.circle_result_table.blockSignals(False)
        self.circle_detail_label.setText("请选择端面查看详细结果")

    def _on_circle_result_selection_changed(self):
        """只刷新右侧详情和规则统计，不改动左侧叠加图。"""

        # 编辑后立即切换端面时，延迟复判定定时器可能尚未触发；
        # 切换前先同步，避免按旧配置重建规则表。
        self._sync_rule_config_from_table()
        result = self._image_inspection_result
        if result is None:
            return
        row = self.circle_result_table.currentRow()
        if row < 0 or row >= len(result.circle_results):
            self._selected_circle_result = None
            self.circle_detail_label.setText("请选择端面查看详细结果")
            self._update_rule_results(None, self._inspection_config)
            return

        circle_result = result.circle_results[row]
        self._selected_circle_result = circle_result
        self.circle_detail_label.setText(
            _circle_result_detail(circle_result)
        )
        self._update_rule_results(circle_result, self._inspection_config)

    def _sync_rule_config_from_table(self) -> bool:
        """将规则表当前值同步到面板内存配置，返回是否成功。"""

        # 结果展示可能先于配置表初始化（例如历史/测试结果恢复）；
        # 此时不能用空的区域表重建规则。
        if self.region_table.rowCount() <= 0:
            return False

        # 某些结果恢复场景可能尚未经过模型加载回调；此时仍可从已保存
        # 的规则中恢复类别集合，避免 build_inspection_config() 生成空规则。
        base_config = self._base_inspection_config
        if (
            not getattr(base_config, "region_rules", None)
            and getattr(self._inspection_config, "region_rules", None)
        ):
            base_config = self._inspection_config
            self._base_inspection_config = copy.deepcopy(base_config)
        if not self._model_class_names:
            self._model_class_names = {
                int(rule.class_id): str(rule.class_name)
                for rule in getattr(
                    base_config,
                    "region_rules",
                    [],
                )
            }
        try:
            config = self.build_inspection_config()
        except ValueError:
            return False
        self._inspection_config = config
        self._base_inspection_config = copy.deepcopy(config)
        return True

    def _update_image_circle_controls(self, result: ImageInspectionResult):
        """设置多端面找圆控件；端面人工确认留到后续步骤。"""

        self._circle_operation_busy = False
        self.find_circle_btn.setText("重新找圆")
        circles = [
            item.circle_candidate
            for item in result.circle_results
            if item.circle_candidate is not None
        ]
        self.circle_candidate_combo.blockSignals(True)
        try:
            self.circle_candidate_combo.clear()
            for index, circle in enumerate(circles, start=1):
                self.circle_candidate_combo.addItem(
                    f"圆 {index}：中心({circle.center_x:.1f}, "
                    f"{circle.center_y:.1f})，R={circle.radius_px:.1f}px，"
                    f"评分={circle.score:.3f}"
                )
            if not circles:
                self.circle_candidate_combo.addItem("尚无候选圆")
            self.circle_candidate_combo.setCurrentIndex(-1)
        finally:
            self.circle_candidate_combo.blockSignals(False)

        self.find_circle_btn.setEnabled(self._original_image is not None)
        self.confirm_circle_btn.setEnabled(False)
        self.confirm_circle_btn.setText("多端面确认暂未开放")
        status_text, _color = _status_display(result.status)
        self.state_badge.setText(f"整图{status_text}")
        self.state_badge.setToolTip("")

    def _update_circle_controls(self, result):
        """按位置展示选中圆；当前项指向其中评分最高的圆。"""

        self._circle_operation_busy = False
        self.find_circle_btn.setText("重新找圆")
        candidates = list(getattr(result, "circle_candidates", []) or [])
        selected_index = getattr(result, "selected_circle_index", None)
        self.circle_candidate_combo.blockSignals(True)
        try:
            self.circle_candidate_combo.clear()
            if not candidates:
                self.circle_candidate_combo.addItem("尚无候选圆")
            else:
                for index, candidate in enumerate(candidates):
                    self.circle_candidate_combo.addItem(
                        f"候选 {index + 1}："
                        f"中心({candidate.center_x:.1f}, {candidate.center_y:.1f})，"
                        f"R={candidate.radius_px:.1f}px，"
                        f"评分={candidate.score:.3f}"
                    )
                if isinstance(selected_index, int) and (
                    0 <= selected_index < len(candidates)
                ):
                    self.circle_candidate_combo.setCurrentIndex(selected_index)
        finally:
            self.circle_candidate_combo.blockSignals(False)

        has_image = self._original_image is not None
        selected_valid = (
            isinstance(selected_index, int)
            and 0 <= selected_index < len(candidates)
        )
        confirmed = bool(getattr(result, "circle_confirmed", False))
        self.find_circle_btn.setEnabled(has_image)
        self.confirm_circle_btn.setEnabled(
            has_image and selected_valid and not confirmed
        )
        if confirmed:
            self.confirm_circle_btn.setText("当前圆心已确认")
            self.state_badge.setText("圆心已确认")
        else:
            self.confirm_circle_btn.setText("确认当前圆心")
            self.state_badge.setText(
                "圆心待确认" if selected_valid else "未找到圆"
            )
        self.state_badge.setToolTip("")

    def _refresh_result_image(self, _value=None, reset_view=False):
        if self._original_image is None:
            return

        mode = self.display_mode_combo.currentText()
        if mode == "原始最终图":
            display_image = self._original_image.copy()
        else:
            background = "black" if mode == "仅缺陷轮廓" else "original"
            if self._image_inspection_result is not None:
                display_image = render_image_inspection_overlay(
                    self._original_image,
                    self._image_inspection_result,
                    self._inspection_config,
                    background=background,
                    show_contours=self.show_masks_check.isChecked(),
                    show_circle=self.show_circle_check.isChecked(),
                    show_rings=self.show_rings_check.isChecked(),
                    show_rois=True,
                )
            else:
                display_image = render_inspection_overlay(
                    self._original_image,
                    self._inspection_result,
                    self._inspection_config,
                    background=background,
                    show_contours=self.show_masks_check.isChecked(),
                    show_circle=self.show_circle_check.isChecked(),
                    show_rings=self.show_rings_check.isChecked(),
                )

        pixmap = ImageWidget._numpy_to_pixmap(display_image)
        self._image_item.setPixmap(pixmap)
        self._image_scene.setSceneRect(self._image_item.boundingRect())
        if reset_view or not self._image_view.user_zoomed:
            self._image_view.reset_zoom()

    def _fit_result_image(self):
        self._image_view.reset_zoom()

    def _show_result_one_to_one(self):
        if self._image_item.pixmap().isNull():
            return
        self._image_view.resetTransform()
        self._image_view._zoom_steps = 0

    def _show_original_image_context_menu(self, position):
        """在检测图像上提供原始最终图保存入口。"""

        if self._original_image is None:
            return
        menu = QMenu(self)
        save_action = menu.addAction("保存原始最终图…")
        selected_action = menu.exec_(self._image_view.mapToGlobal(position))
        if selected_action is save_action:
            self._save_original_image()

    def _save_original_image(self):
        """以最高质量 JPEG 保存未绘制的原始最终图。"""

        if self._original_image is None:
            return

        default_name = datetime.now().strftime("final_image_%Y%m%d_%H%M%S.jpg")
        path, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "保存原始最终图",
            default_name,
            "JPEG 图像 (*.jpg *.jpeg)",
        )
        if not path:
            return

        output_path = Path(path)
        if not output_path.suffix:
            output_path = output_path.with_suffix(".jpg")
        if output_path.suffix.lower() not in {".jpg", ".jpeg"}:
            self.original_image_save_failed.emit(
                "原始最终图仅支持保存为 JPG/JPEG"
            )
            return

        try:
            # imencode + tofile 能正确处理 Windows 中文路径；质量 100 为
            # OpenCV JPEG 可设置的最高质量。_original_image 未经过叠加绘制。
            import cv2

            succeeded, encoded = cv2.imencode(
                ".jpg",
                self._original_image,
                [cv2.IMWRITE_JPEG_QUALITY, 100],
            )
            if not succeeded:
                raise RuntimeError("JPEG 编码失败")
            encoded.tofile(str(output_path))
            self.original_image_saved.emit(str(output_path))
        except (OSError, RuntimeError, ValueError) as error:
            self.original_image_save_failed.emit(str(error))

    def _update_result_summary(self, task_id: str, result):
        status_value = getattr(getattr(result, "status", None), "value", "")
        verdict = {
            "PASS": "合格",
            "FAIL": "不合格",
            "PENDING": "待确认",
            "ERROR": "检测错误",
        }.get(status_value, "待确认")
        self.verdict_label.setText(verdict)

        if status_value == "FAIL":
            self.verdict_label.setStyleSheet(
                "color: #dc2626; font-size: 30px; font-weight: 700;"
            )
        elif status_value == "PASS":
            self.verdict_label.setStyleSheet(
                "color: #16a34a; font-size: 30px; font-weight: 700;"
            )
        elif status_value == "ERROR":
            self.verdict_label.setStyleSheet(
                "color: #ea580c; font-size: 30px; font-weight: 700;"
            )
        else:
            self.verdict_label.setStyleSheet(
                "color: #64748b; font-size: 30px; font-weight: 700;"
            )
        
        self.verdict_detail_label.setText(f"任务：{task_id}")

        region_results = getattr(result, "region_results", []) or []
        if region_results:
            instance_count = sum(
                int(getattr(item, "valid_instance_count", 0))
                for item in region_results
            )
        else:
            instance_count = len(getattr(result, "instances", []) or [])
        self.instance_count_label.setText(str(instance_count))

        circle = _selected_circle(result)
        if circle is None:
            self.circle_result_label.setText("未找到")
        else:
            confirm_text = (
                "已确认" if getattr(result, "circle_confirmed", False)
                else "待确认"
            )
            self.circle_result_label.setText(
                f"{confirm_text}，R={float(circle.radius_px):.1f}px，"
                f"评分={float(circle.score):.3f}"
            )

        width = int(getattr(result, "image_width", 0) or 0)
        height = int(getattr(result, "image_height", 0) or 0)
        self.image_size_label.setText(
            f"{width} × {height}" if width and height else "--"
        )
        timings = getattr(result, "timings_ms", {}) or {}
        self.inference_time_label.setText(_format_ms(timings.get("inference")))
        self.total_time_label.setText(_format_ms(timings.get("total")))

    def _update_image_result_summary(
        self,
        task_id: str,
        result: ImageInspectionResult,
    ):
        status_value = getattr(result.status, "value", "")
        verdict = {
            "PASS": "合格",
            "FAIL": "不合格",
            "PENDING": "待确认",
            "ERROR": "检测错误",
        }.get(status_value, "待确认")
        self.verdict_label.setText(verdict)
        if status_value == "FAIL":
            color = "#dc2626"
        elif status_value == "PASS":
            color = "#16a34a"
        elif status_value == "ERROR":
            color = "#ea580c"
        else:
            color = "#64748b"
        self.verdict_label.setStyleSheet(
            f"color: {color}; font-size: 30px; font-weight: 700;"
        )
        self.verdict_detail_label.setText(
            f"任务：{task_id}\n"
            f"端面：{result.detected_circle_count}/"
            f"{result.expected_circle_count}，"
            f"已处理：{result.completed_circle_count}"
        )
        self.instance_count_label.setText(str(sum(
            len(item.instances)
            for item in result.circle_results
        )))
        self.circle_result_label.setText(
            f"检测 {result.detected_circle_count}/"
            f"{result.expected_circle_count}，"
            f"完成 {result.completed_circle_count}"
        )
        self.image_size_label.setText(
            f"{result.image_width} × {result.image_height}"
            if result.image_width and result.image_height else "--"
        )
        self.inference_time_label.setText(
            _format_ms(result.timings_ms.get("inference"))
        )
        self.total_time_label.setText(
            _format_ms(result.timings_ms.get("total"))
        )

    def _update_rule_results(self, result, config):
        rules = list(getattr(config, "region_rules", []) or [])
        results = {
            (item.region_id, item.class_id): item
            for item in (getattr(result, "region_results", []) or [])
        }
        self._populate_rule_table(rules, results)

    def _build_circle_group(self) -> QGroupBox:
        group = QGroupBox("2. 深度学习找圆")
        layout = QFormLayout(group)
        self.expected_circle_count_spin = QSpinBox()
        self.expected_circle_count_spin.setRange(1, 20)
        self.expected_circle_count_spin.setValue(1)
        self.circle_confidence_spin = QDoubleSpinBox()
        self.circle_confidence_spin.setRange(0.0, 1.0)
        self.circle_confidence_spin.setDecimals(3)
        self.circle_confidence_spin.setSingleStep(0.05)
        self.circle_confidence_spin.setValue(0.25)
        self.circle_candidate_combo = QComboBox()
        self.circle_candidate_combo.addItem("尚无候选圆")
        self.circle_candidate_combo.setEnabled(False)
        self.circle_candidate_combo.setToolTip(
            "展示本次 YOLO 找圆得到并用于生成 ROI 的产品圆"
        )
        self.find_circle_btn = QPushButton("重新找圆")
        self.confirm_circle_btn = QPushButton("确认当前圆心")
        layout.addRow("预期圆数量：", self.expected_circle_count_spin)
        layout.addRow("最低置信度：", self.circle_confidence_spin)
        layout.addRow("候选圆：", self.circle_candidate_combo)
        layout.addRow(self.find_circle_btn)
        layout.addRow(self.confirm_circle_btn)
        return group

    def _build_region_group(self) -> QGroupBox:
        group = QGroupBox("3. 标定与质检圆环")
        layout = QVBoxLayout(group)
        scale_form = QFormLayout()
        self.mm_per_pixel_spin = QDoubleSpinBox()
        self.mm_per_pixel_spin.setDecimals(6)
        self.mm_per_pixel_spin.setRange(0.0, 1000.0)
        self.mm_per_pixel_spin.setSingleStep(0.001)
        self.mm_per_pixel_spin.setSuffix(" um/px")
        scale_form.addRow("物理比例：", self.mm_per_pixel_spin)
        layout.addLayout(scale_form)

        self.region_table = QTableWidget(2, 3)
        self.region_table.setHorizontalHeaderLabels([
            "区域名称",
            "内半径 um",
            "外半径 um",
        ])
        region_rows = [
            ("中心区", "0.0", "10.0"),
            ("外环区", "10.0", "20.0"),
        ]
        for row, values in enumerate(region_rows):
            for column, text in enumerate(values):
                self.region_table.setItem(row, column, QTableWidgetItem(text))
        self.region_table.verticalHeader().setVisible(False)
        self.region_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )
        self.region_table.setMinimumHeight(120)
        layout.addWidget(self.region_table)

        buttons = QHBoxLayout()
        self.add_region_btn = QPushButton("新增圆环")
        self.remove_region_btn = QPushButton("删除圆环")
        buttons.addWidget(self.add_region_btn)
        buttons.addWidget(self.remove_region_btn)
        layout.addLayout(buttons)
        return group

    def _build_history_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        filter_row = QHBoxLayout()
        self.history_filter_combo = QComboBox()
        self.history_filter_combo.addItems([
            "全部结果",
            "仅不合格",
            "仅合格",
            "待确认 / 错误",
        ])
        self.refresh_history_btn = QPushButton("刷新")
        filter_row.addWidget(self.history_filter_combo, 1)
        filter_row.addWidget(self.refresh_history_btn)
        layout.addLayout(filter_row)

        self.history_list = QListWidget()
        self.history_list.addItem(
            "尚无历史检测记录\n"
            "后续将显示时间、缩略图、判定和模型名称"
        )
        layout.addWidget(self.history_list, 1)

        self.open_history_dir_btn = QPushButton("打开历史目录")
        layout.addWidget(self.open_history_dir_btn)
        return page

    def _apply_preview_state(self):
        """框架阶段禁用所有会触发业务动作的按钮。"""

        action_buttons = [
            self.refresh_history_btn,
            self.open_history_dir_btn,
        ]
        for button in action_buttons:
            button.setEnabled(False)
            button.setToolTip("当前为 GUI 框架预览，业务逻辑将在后续步骤接入")

        self.select_model_btn.setToolTip("选择一个 YOLO-Seg .pt 模型")
        self.select_local_image_btn.setToolTip(
            "选择一张本地图片，使用当前检测参数执行完整质检"
        )
        self.load_model_btn.setEnabled(False)
        self.add_region_btn.setToolTip("在末尾增加一个连续圆环")
        self.remove_region_btn.setToolTip("删除选中圆环，未选择时删除最后一行")
        self.find_circle_btn.setEnabled(False)
        self.find_circle_btn.setToolTip(
            "按当前参数重新执行轮廓找圆，并对新的 ROI 重新推理"
        )
        self.confirm_circle_btn.setEnabled(False)
        self.confirm_circle_btn.setToolTip(
            "确认评分最高的候选圆，然后重新执行规则判定"
        )
        self.fit_image_btn.setEnabled(False)
        self.reset_view_btn.setEnabled(False)

    def _apply_style(self):
        self.setStyleSheet(
            """
            QLabel#inspectionTitle {
                font-size: 18px;
                font-weight: 600;
            }
            QLabel#inspectionSubtitle {
                color: #6b7280;
                padding-left: 8px;
            }
            QLabel#inspectionStateBadge {
                color: #92400e;
                background: #fef3c7;
                border: 1px solid #f59e0b;
                border-radius: 10px;
                padding: 4px 12px;
                min-width: 82px;
            }
            QFrame#inspectionImageFrame {
                background: #171a20;
                border: 1px solid #3f4652;
                border-radius: 4px;
            }
            QLabel#inspectionImagePlaceholder {
                color: #aeb6c2;
                font-size: 14px;
                line-height: 1.5;
            }
            QFrame#inspectionVerdictBox {
                background: #f8fafc;
                border: 1px solid #d8dee8;
                border-radius: 5px;
            }
            QLabel#inspectionVerdict {
                color: #64748b;
                font-size: 30px;
                font-weight: 700;
            }
            QGroupBox {
                font-weight: 600;
            }
            QGroupBox QLabel, QGroupBox QCheckBox {
                font-weight: 400;
            }
            """
        )


def _format_ms(value) -> str:
    if value is None:
        return "--"
    try:
        return f"{float(value):.0f} ms"
    except (TypeError, ValueError):
        return "--"


def _normalize_class_names(names) -> dict[int, str]:
    """兼容 Ultralytics 返回的字典或列表类别名称。"""

    if isinstance(names, dict):
        items = names.items()
    elif isinstance(names, (list, tuple)):
        items = enumerate(names)
    else:
        return {}

    normalized = {}
    for class_id, class_name in items:
        try:
            normalized[int(class_id)] = str(class_name)
        except (TypeError, ValueError):
            continue
    return dict(sorted(normalized.items()))


def _readonly_item(text: str) -> QTableWidgetItem:
    item = QTableWidgetItem(str(text))
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    return item


def _selected_circle(result):
    candidates = getattr(result, "circle_candidates", []) or []
    index = getattr(result, "selected_circle_index", None)
    if not isinstance(index, int) or index < 0 or index >= len(candidates):
        return None
    return candidates[index]


def _status_display(status) -> tuple[str, str]:
    value = getattr(status, "value", str(status or ""))
    return {
        "PASS": ("合格", "#16a34a"),
        "FAIL": ("不合格", "#dc2626"),
        "PENDING": ("待确认", "#d97706"),
        "ERROR": ("检测错误", "#b91c1c"),
    }.get(value, ("待确认", "#64748b"))


def _circle_result_detail(circle_result) -> str:
    status_text, _color = _status_display(circle_result.status)
    lines = [
        f"当前端面：{circle_result.circle_id or '--'} / {status_text}",
    ]
    circle = circle_result.circle_candidate
    if circle is None:
        lines.append("圆：未找到")
    else:
        lines.append(
            f"圆心：({circle.center_x:.1f}, {circle.center_y:.1f}) px，"
            f"R={circle.radius_px:.1f} px，评分={circle.score:.3f}"
        )
    roi = circle_result.roi
    if roi is None:
        lines.append("ROI：--")
    else:
        lines.append(
            f"ROI：x={roi.x}, y={roi.y}, "
            f"{roi.width}×{roi.height} px"
        )
    lines.append(f"分割实例：{len(circle_result.instances)}")
    if circle_result.error:
        lines.append(f"错误：{circle_result.error}")
    if circle_result.failure_reasons:
        lines.append("判定原因：" + "；".join(circle_result.failure_reasons))
    return "\n".join(lines)
