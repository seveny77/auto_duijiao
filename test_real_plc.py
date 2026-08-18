# test/test_real_plc.py
"""真实 PLC 递进测试 —— 从简单到复杂，每步确认通了再往下走"""
from plc.client import PlcClient
from plc.protocol import *
import time

# ══════════════════════════════════════
# 你自己填
# ══════════════════════════════════════
PLC_HOST = "192.168.100.88"   # ← 填 PLC IP
PLC_PORT = 502             # ← 填端口

client = PlcClient(PLC_HOST, PLC_PORT)

# ══════════════════════════════════════
# 第 1 步：能不能连上
# ══════════════════════════════════════
print("=" * 50)
print("第1步：测试连接")
print("=" * 50)

try:
    client.connect()
    print("✅ 连接成功")
except Exception as e:
    print(f"❌ 连接失败: {e}")
    print("\n请检查：")
    print("  - IP 地址和端口是否正确")
    print("  - 网络是否 ping 得通")
    print("  - PLC 是否上电运行")
    exit(1)

input("\n按回车继续第2步...")

# ══════════════════════════════════════
# 第 2 步：读 PLC 已有的值（不写，只读）
#    先读一个已知的寄存器，验证地址映射是否正确
#    D4103-D4106 行程范围是 PLC 自己写的，读出来看看
# ══════════════════════════════════════
print("\n" + "=" * 50)
print("第2步：读取 PLC 已有数据（行程范围）")
print("=" * 50)

try:
    stroke_min = client.read_u32(REG_STROKE_MIN_LO)
    stroke_max = client.read_u32(REG_STROKE_MAX_LO)
    feipai_queren = client.read_register(REG_FLYSCAN_CONFIRM)
    print(f"✅ 行程最小值: {stroke_min} μm")
    print(f"✅ 行程最大值: {stroke_max} μm")
    print(f"✅ 飞拍确认: {feipai_queren} ")


    if stroke_min == 0 and stroke_max == 0:
        print("⚠️  两个值都是 0，可能是地址偏移不对")
        print("    尝试方案：把 protocol.py 里所有 -1 改成 -0 再试")
except Exception as e:
    print(f"❌ 读取失败: {e}")
    print("  可能在 protocol.py 的地址偏移需要调整")

input("\n按回车继续第3步...")
#
# ══════════════════════════════════════
# 第 3 步：写一个值再读回来（只测 VA→PLC 方向）
#    找一个不影响 PLC 运行的地址来测，比如 D4008 触发间隔
#    先记下原值，写完读回来确认，再恢复原值
# ══════════════════════════════════════
print("\n" + "=" * 50)
print("第3步：读写单个寄存器")
print("=" * 50)

try:
    # 读原值
    original = client.read_register(REG_FLYSCAN_INTERVAL)
    print(f"  D4008 原值: {original}")

    # 写测试值
    test_val = 123
    client.write_register(REG_FLYSCAN_INTERVAL, test_val)

    # 读回来验证
    read_back = client.read_register(REG_FLYSCAN_INTERVAL)
    if read_back == test_val:
        print(f"✅ 写入 {test_val}，读回 {read_back}，一致")
    else:
        print(f"❌ 写入 {test_val}，读回 {read_back}，不一致！")

    # 恢复原值
    client.write_register(REG_FLYSCAN_INTERVAL, original)
    print(f"  已恢复原值: {original}")

except Exception as e:
    print(f"❌ 失败: {e}")

input("\n按回车继续第4步...")
#
# ══════════════════════════════════════
# 第 4 步：测试 32 位读写
#    对 D4006-D4007（飞拍步距）写一个值再读回
# ══════════════════════════════════════
print("\n" + "=" * 50)
print("第4步：32位读写")
print("=" * 50)

try:
    original = client.read_u32(REG_FLYSCAN_STEP_LO)
    print(f"  D4006-D4007 原值: {original}")

    test_val = 50
    client.write_u32(REG_FLYSCAN_STEP_LO, test_val)
    read_back = client.read_u32(REG_FLYSCAN_STEP_LO)

    if read_back == test_val:
        print(f"✅ 写入 {test_val}，读回 {read_back}，一致")
    else:
        print(f"❌ 写入 {test_val}，读回 {read_back}，不一致！")
        print("  可能是字节序不对，试试把 protocol.py 里的 pack_u32 改成大端")

    # 恢复原值
    client.write_u32(REG_FLYSCAN_STEP_LO, original)
    print(f"  已恢复原值: {original}")

except Exception as e:
    print(f"❌ 失败: {e}")

input("\n按回车继续第5步...")
#
# ══════════════════════════════════════
# 第 5 步：飞拍完整握手
#    ⚠️ 这一步真的会触发 PLC 运动！
#    建议先确认 PLC 处于安全状态
# ══════════════════════════════════════
print("\n" + "=" * 50)
print("第5步：飞拍完整握手 ⚠️ 会触发PLC运动")
print("=" * 50)

confirm = input("确认要触发飞拍吗？输入 yes 继续: ")
if confirm.lower() != "yes":
    print("已跳过")
else:
    try:
        count = client.flyscan_trigger(
            start_pos_um=11000,
            end_pos_um=12000,
            step_um=50,
            timeout_s=600
        )
        feipai_queren = client.read_register(REG_FLYSCAN_CONFIRM)
        print(f"✅ 飞拍确认: {feipai_queren} ")
        print(f"✅ 飞拍完成，拍到 {count} 张")
    except Exception as e:
        print(f"❌ 飞拍失败: {e}")

# ══════════════════════════════════════
# 收尾
# ══════════════════════════════════════
print("\n" + "=" * 50)
print("测试完成")
print("=" * 50)
client.disconnect()
print("已断开")
