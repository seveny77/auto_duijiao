"""曲线面板：matplotlib 画布 + 分组框。"""
import matplotlib
matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "SimSun"]
matplotlib.rcParams["axes.unicode_minus"] = False

from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PyQt5.QtWidgets import QGroupBox, QVBoxLayout


class CurveCanvas(FigureCanvasQTAgg):
    def __init__(self, parent=None):
        self.fig = Figure(figsize=(4, 4), dpi=100) #纸
        self.ax = self.fig.add_subplot(111) #格子
        super().__init__(self.fig) #纸交给桥
        self.setParent(parent)
        self._setup_axes()

    def _setup_axes(self):
        self.ax.set_xlabel("位置 (µm)")
        self.ax.set_ylabel("清晰度得分")
        self.ax.set_title("清晰度曲线")
        self.ax.grid(alpha=0.3)
        self.fig.tight_layout()

    def clear_curve(self):
        self.ax.clear()
        self._setup_axes()
        self.draw_idle()

    def plot_points(self, points, label, color, marker="o"):
        if not points:
            return
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        self.ax.scatter(xs, ys, label=label, color=color,
                        marker=marker, s=24, zorder=3)
        self.ax.legend(loc="best")
        self.draw_idle()

    def plot_peak(self, x, label="预测峰"):
        self.ax.axvline(x, color="red", linestyle="--", lw=1, label=label)
        self.ax.legend(loc="best")
        self.draw_idle()

class CurvePanel(QGroupBox):
    """清晰度曲线面板：把画布装进分组框，暴露简单 API。"""

    def __init__(self, parent=None):
        super().__init__("清晰度曲线", parent)
        layout = QVBoxLayout(self)
        self._canvas = CurveCanvas()
        layout.addWidget(self._canvas)
        self.setFixedWidth(420)          # 固定宽度，保持原布局

    def clear_curve(self):
        self._canvas.clear_curve()

    def plot_points(self, points, label, color, marker="o"):
        self._canvas.plot_points(points, label, color, marker)

    def plot_peak(self, x, label="预测峰"):
        self._canvas.plot_peak(x, label)