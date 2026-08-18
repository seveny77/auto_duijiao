# ============================================================
# 地址偏移（pymodbus 从 0 开始，PLC 编号从 1 开始）
# 公式: ADDR = D编号 - 1
# ============================================================

# -- VA → PLC --
# ============================================================
# 地址偏移（pymodbus 从 0 开始，PLC 编号从 1 开始）
# 公式: ADDR = D编号 - 1
# ============================================================

# -- VA → PLC --
# ============================================================
# 地址映射：D编号 = Modbus 地址（汇川无需 -1）
# ============================================================

# -- VA → PLC --
REG_VA_HEARTBEAT        = 4000    # D4000  VA心跳
REG_FLYSCAN_START_LO    = 4002    # D4002  飞拍开始位置（32位μm, 低16位）
REG_FLYSCAN_START_HI    = 4003    # D4003  飞拍开始位置（32位μm, 高16位）
REG_FLYSCAN_END_LO      = 4004    # D4004  飞拍结束位置（32位μm, 低16位）
REG_FLYSCAN_END_HI      = 4005    # D4005  飞拍结束位置（32位μm, 高16位）
REG_FLYSCAN_STEP_LO     = 4006    # D4006  飞拍步距（32位μm, 低16位）
REG_FLYSCAN_STEP_HI     = 4007    # D4007  飞拍步距（32位μm, 高16位）
REG_FLYSCAN_INTERVAL    = 4008    # D4008  触发间隔（ms），写0由PLC按步距/速度自动计算
REG_FLYSCAN_TRIGGER     = 4009    # D4009  飞拍触发（VA置1开始飞拍）
REG_MOVE_INDEX          = 4010    # D4010  定点目标序号（VA写入目标index）
REG_MOVE_TRIGGER        = 4011    # D4011  定点触发（VA置1开始移动）
REG_PROCESS_COMPLETE    = 4012    # D4012  流程完成（VA置1本轮对焦结束）

# -- PLC → VA --
REG_PLC_HEARTBEAT       = 4100    # D4100  PLC心跳
REG_STROKE_MIN_LO       = 4103    # D4103  丝杆物理最小位置μm（32位, 低16位）
REG_STROKE_MIN_HI       = 4104    # D4104  丝杆物理最小位置μm（32位, 高16位）
REG_STROKE_MAX_LO       = 4105    # D4105  丝杆物理最大位置μm（32位, 低16位）
REG_STROKE_MAX_HI       = 4106    # D4106  丝杆物理最大位置μm（32位, 高16位）
REG_FLYSCAN_CONFIRM     = 4109    # D4109  飞拍确认（PLC置1，已收到指令并开始）
REG_FLYSCAN_DONE        = 4110    # D4110  飞拍完成（PLC置1，全部拍照完成）
REG_FLYSCAN_COUNT       = 4111    # D4111  飞拍张数（PLC写入实际拍到的张数）
REG_MOVE_CONFIRM        = 4112    # D4112  定点确认（PLC置1，已收到指令）
REG_MOVE_DONE           = 4113    # D4113  定点完成（PLC置1，移动+拍照完成）



def pack_u32(value: int) -> tuple:
    """32位 → (低16位, 高16位)，小端"""
    low = value & 0xFFFF
    high = (value >> 16) & 0xFFFF
    return (low, high)

def unpack_u32(low: int, high: int) -> int:
    """(低16位, 高16位) → 32位，小端"""
    return (high << 16) | low