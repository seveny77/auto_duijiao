"""当前连续自动对焦所需的参数面板。"""

from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QVBoxLayout,
    QWidget,
)

from backend.constants import SENSOR_H, SENSOR_W


class ParamPanel(QWidget):
    """仅保留连续精扫的硬件、采集与保存参数。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(320)
        layout = QVBoxLayout(self)
        layout.addWidget(self._build_motion_group())
        layout.addWidget(self._build_camera_group())
        layout.addWidget(self._build_run_group())
        layout.addWidget(self._build_scan_group())
        layout.addStretch(1)

    def _build_motion_group(self):
        group = QGroupBox("运动控制器")
        form = QFormLayout(group)
        self.motion_connect_btn = QPushButton("连接运动控制器")
        self.motion_reset_btn = QPushButton("复位报警")
        self.motion_servo_btn = QPushButton("伺服使能")
        self.motion_home_btn = QPushButton("回原点")
        self.motion_stop_btn = QPushButton("停止运动")
        self.motion_connection_label = QLabel("未连接")
        self.motion_servo_label = QLabel("未使能")
        self.motion_home_label = QLabel("未回零")
        self.motion_axis_label = QLabel("未连接")
        self.motion_position_label = QLabel("--")
        self.motion_stroke_label = QLabel("未连接")
        maintenance = QHBoxLayout()
        maintenance.addWidget(self.motion_reset_btn)
        maintenance.addWidget(self.motion_servo_btn)
        motion = QHBoxLayout()
        motion.addWidget(self.motion_home_btn)
        motion.addWidget(self.motion_stop_btn)
        form.addRow("连接状态:", self.motion_connection_label)
        form.addRow("伺服状态:", self.motion_servo_label)
        form.addRow("回零状态:", self.motion_home_label)
        form.addRow("轴状态:", self.motion_axis_label)
        form.addRow("当前位置:", self.motion_position_label)
        form.addRow(self.motion_connect_btn)
        form.addRow(maintenance)
        form.addRow(motion)
        form.addRow("行程范围:", self.motion_stroke_label)
        return group

    def _build_camera_group(self):
        group = QGroupBox("相机与硬件 ROI")
        form = QFormLayout(group)
        self.exposure_spin = QSpinBox()
        self.exposure_spin.setRange(10, 100000)
        self.exposure_spin.setValue(3000)
        self.exposure_spin.setSuffix(" µs")
        self.gain_spin = QDoubleSpinBox()
        self.gain_spin.setRange(0.0, 30.0)
        self.gain_spin.setDecimals(1)
        self.gain_spin.setSuffix(" dB")
        self.decimation_combo = QComboBox()
        self.decimation_combo.addItems(["1x1", "2x2", "4x4"])
        self.work_roi_width_spin = QSpinBox()
        self.work_roi_width_spin.setRange(0, SENSOR_W)
        self.work_roi_width_spin.setSingleStep(32)
        self.work_roi_width_spin.setSpecialValueText("全幅")
        self.work_roi_height_spin = QSpinBox()
        self.work_roi_height_spin.setRange(0, SENSOR_H)
        self.work_roi_height_spin.setSingleStep(32)
        self.work_roi_height_spin.setSpecialValueText("全幅")
        self.camera_connect_btn = QPushButton("连接相机")
        self.camera_roi_apply_btn = QPushButton("应用相机 ROI")
        self.camera_connection_label = QLabel("未连接")
        self.camera_roi_status_label = QLabel("未应用")
        form.addRow("曝光时间:", self.exposure_spin)
        form.addRow("增益:", self.gain_spin)
        form.addRow("下采样(dec):", self.decimation_combo)
        form.addRow("开窗宽度:", self.work_roi_width_spin)
        form.addRow("开窗高度:", self.work_roi_height_spin)
        form.addRow("连接状态:", self.camera_connection_label)
        form.addRow(self.camera_connect_btn)
        form.addRow("硬件 ROI 状态:", self.camera_roi_status_label)
        form.addRow(self.camera_roi_apply_btn)
        return group

    def _build_run_group(self):
        group = QGroupBox("运行模式")
        form = QFormLayout(group)
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["真实", "仿真"])
        self.skip_confirm_check = QCheckBox("跳过运动确认")
        self.skip_confirm_check.setChecked(True)
        form.addRow("模式:", self.mode_combo)
        form.addRow(self.skip_confirm_check)
        return group

    def _build_scan_group(self):
        group = QGroupBox("连续精扫参数")
        form = QFormLayout(group)
        self.search_start_spin = QSpinBox()
        self.search_start_spin.setRange(0, 50000)
        self.search_start_spin.setValue(9500)
        self.search_span_spin = QSpinBox()
        self.search_span_spin.setRange(100, 50000)
        self.search_span_spin.setValue(2000)
        self.continuous_velocity_spin = QDoubleSpinBox()
        self.continuous_velocity_spin.setRange(1.0, 5000.0)
        self.continuous_velocity_spin.setDecimals(1)
        self.continuous_velocity_spin.setValue(50.0)
        self.continuous_velocity_spin.setSuffix(" µm/s")
        self.soft_trigger_interval_spin = QDoubleSpinBox()
        self.soft_trigger_interval_spin.setRange(1.0, 200.0)
        self.soft_trigger_interval_spin.setDecimals(1)
        self.soft_trigger_interval_spin.setValue(20.0)
        self.soft_trigger_interval_spin.setSuffix(" fps")
        self.soft_trigger_timeout_spin = QDoubleSpinBox()
        self.soft_trigger_timeout_spin.setRange(0.1, 10.0)
        self.soft_trigger_timeout_spin.setDecimals(1)
        self.soft_trigger_timeout_spin.setValue(1.0)
        self.soft_trigger_timeout_spin.setSuffix(" s")
        self.save_edit = QLineEdit()
        self.save_edit.setPlaceholderText("留空 = 不保存最佳原图")
        self.start_btn = QPushButton("开始连续对焦")
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setEnabled(False)
        buttons = QHBoxLayout()
        buttons.addWidget(self.start_btn)
        buttons.addWidget(self.stop_btn)
        form.addRow("扫描起点(µm):", self.search_start_spin)
        form.addRow("扫描跨度(µm):", self.search_span_spin)
        form.addRow("扫描速度:", self.continuous_velocity_spin)
        form.addRow("连续采集帧率:", self.soft_trigger_interval_spin)
        form.addRow("首帧等待超时:", self.soft_trigger_timeout_spin)
        form.addRow("最终图保存目录:", self.save_edit)
        form.addRow(buttons)
        return group

    def lock_widgets(self):
        """对焦期间锁住会改变本轮硬件和采集行为的控件。"""

        return [
            self.mode_combo, self.exposure_spin, self.gain_spin,
            self.decimation_combo, self.work_roi_width_spin,
            self.work_roi_height_spin, self.camera_connect_btn,
            self.camera_roi_apply_btn, self.search_start_spin,
            self.search_span_spin, self.continuous_velocity_spin,
            self.soft_trigger_interval_spin, self.soft_trigger_timeout_spin,
            self.save_edit,
        ]
