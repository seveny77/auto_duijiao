"""连续自动对焦的紧凑 CT 日志。"""


class CtLogger:
    _LABELS = {
        "start_position_ms": "起点定位",
        "collector_start_ms": "采集启动",
        "continuous_motion_ms": "连续扫描运动",
        "collector_stop_and_drain_ms": "停止与排空",
        "capture_score_avg_ms": "平均清晰度计算",
        "focus_total_ms": "对焦CT",
        "return_to_start_ms": "回起点",
        "total_with_return_ms": "含回位总计",
    }

    def __init__(self, message_fn=None):
        self._message_fn = message_fn

    def log(self, ct):
        if not ct or self._message_fn is None:
            return
        parts = [
            f"{label}={float(ct[key]):.1f}ms"
            for key, label in self._LABELS.items() if key in ct
        ]
        if parts:
            self._message_fn("[CT] " + " | ".join(parts))
