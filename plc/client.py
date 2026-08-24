import logging
import time

from pymodbus.client import ModbusTcpClient

from .protocol import *


logger = logging.getLogger(__name__)


class PlcTimeoutError(Exception): ...
class PlcConnectionError(Exception): ...

class PlcClient:
    def __init__(self, host="192.168.1.100", port=502, timeout=5.0):
        self._host = host
        self._port = port
        self._timeout = timeout
        self._client = None

    def connect(self):
        self._client = ModbusTcpClient(
            host=self._host, port=self._port, timeout=self._timeout
        )
        if not self._client.connect():
            raise PlcConnectionError(f"无法连接 PLC {self._host}:{self._port}")
        logger.info(
            "PLC 已连接: %s:%d",
            self._host,
            self._port,
        )

    def disconnect(self):
        if self._client:
            self._client.close()
            self._client = None

    def is_connected(self):
        return self._client is not None and self._client.connected
    # 读写单个寄存器
    def read_register(self, addr):
        """读单个 16 位寄存器"""
        rr = self._client.read_holding_registers(addr, count=1)
        if rr.isError():
            raise PlcConnectionError(f"读寄存器 D{addr + 1} 失败")
        return rr.registers[0]

    def write_register(self, addr, value):
        """写单个 16 位寄存器"""
        rq = self._client.write_register(addr, int(value) & 0xFFFF)
        if rq.isError():
            raise PlcConnectionError(f"写寄存器 D{addr + 1} 失败")
    #  32 位读写
    def read_u32(self, addr_lo):
        """读 32 位值，从 addr_lo 开始两个连续寄存器（小端）"""
        rr = self._client.read_holding_registers(addr_lo, count=2)
        if rr.isError():
            raise PlcConnectionError(f"读32位 D{addr_lo + 1} 失败")
        return unpack_u32(rr.registers[0], rr.registers[1])

    def write_u32(self, addr_lo, value):
        """写 32 位值到 addr_lo 开始的两个连续寄存器（小端）"""
        low, high = pack_u32(value)
        rq = self._client.write_registers(addr_lo, values=[low, high])
        if rq.isError():
            raise PlcConnectionError(f"写32位 D{addr_lo + 1} 失败")

    #  读行程范围
    def read_stroke_range(self):
        """返回 (最小μm, 最大μm)"""
        min_val = self.read_u32(REG_STROKE_MIN_LO)
        max_val = self.read_u32(REG_STROKE_MAX_LO)
        return (min_val, max_val)


    def _wait_register(self, addr, expected_value, timeout, poll_interval=0.05):
        """轮询等待某个寄存器变成期望值，超时返回 False"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            val = self.read_register(addr)
            if val == expected_value:
                return True
            time.sleep(poll_interval)
        return False
    # 写飞拍方法
    def flyscan_trigger(self, start_pos_um, end_pos_um,
                        step_um, timeout_s=30):
        """
        触发等步长飞拍，阻塞直到完成。
        返回: 实际拍到的张数
        """
        # ① 防御：确保触发位归零（防上次崩溃残留 1）
        self.write_register(REG_FLYSCAN_TRIGGER, 0)

        # ② 写参数
        self.write_u32(REG_FLYSCAN_START_LO, start_pos_um)
        self.write_u32(REG_FLYSCAN_END_LO, end_pos_um)
        self.write_u32(REG_FLYSCAN_STEP_LO, step_um)

        # ③ 触发（0→1 上升沿）
        self.write_register(REG_FLYSCAN_TRIGGER, 1)

        # ④ 等确认
        if not self._wait_register(REG_FLYSCAN_CONFIRM, 1, timeout=2.0):
            raise PlcTimeoutError("飞拍确认超时")

        # ⑤ 等完成
        if not self._wait_register(REG_FLYSCAN_DONE, 1, timeout=timeout_s):
            raise PlcTimeoutError("飞拍完成超时")

        # ⑥ 读结果
        count = self.read_register(REG_FLYSCAN_COUNT)

        # ⑦ 清触发 + 确认 + 完成（该 PLC 不自清 D4110，直接强制清位，避免阻塞下一条指令）
        self.write_register(REG_FLYSCAN_TRIGGER, 0)
        self.write_register(REG_FLYSCAN_DONE, 0)
        self.write_register(REG_FLYSCAN_CONFIRM, 0)

        return count
    # 定点移动
    def move_to_position(self, index, timeout_s=10):
        """移动到指定 index 并拍照"""
        # ① 触发位先归零，保证后续 0→1 上升沿（PLC 边沿触发）
        self.write_register(REG_MOVE_TRIGGER, 0)
        time.sleep(0.05)

        # ② 清除上次残留的确认/完成位，确保 PLC 状态机就绪
        self.write_register(REG_MOVE_CONFIRM, 0)
        self.write_register(REG_MOVE_DONE, 0)

        # ③ 写目标 index
        self.write_register(REG_MOVE_INDEX, index)

        # ④ 置触发（0→1 上升沿）
        self.write_register(REG_MOVE_TRIGGER, 1)

        # ⑤ 等确认
        if not self._wait_register(REG_MOVE_CONFIRM, 1, timeout=2.0):
            snapshot = {}
            for addr, name in [
                (REG_FLYSCAN_TRIGGER, "D4009飞拍触发"),
                (REG_FLYSCAN_CONFIRM, "D4109飞拍确认"),
                (REG_FLYSCAN_DONE, "D4110飞拍完成"),
                (REG_MOVE_INDEX, "D4010定点序号"),
                (REG_MOVE_TRIGGER, "D4011定点触发"),
                (REG_MOVE_CONFIRM, "D4112定点确认"),
                (REG_MOVE_DONE, "D4113定点完成"),
            ]:
                try:
                    snapshot[name] = self.read_register(addr)
                except Exception:
                    snapshot[name] = "?"
            detail = ", ".join(f"{k}={v}" for k, v in snapshot.items())
            raise PlcTimeoutError(f"定点移动确认超时（寄存器: {detail}）")

        # ⑥ 等完成
        if not self._wait_register(REG_MOVE_DONE, 1, timeout=timeout_s):
            raise PlcTimeoutError("定点移动完成超时")

        # ⑦ 清除，避免残留状态阻塞下一条指令
        self.write_register(REG_MOVE_TRIGGER, 0)
        self.write_register(REG_MOVE_CONFIRM, 0)
        self.write_register(REG_MOVE_DONE, 0)

    # 流程结束
    def process_complete(self):
        """通知 PLC 本轮对焦结束"""
        self.write_register(REG_PROCESS_COMPLETE, 1)



