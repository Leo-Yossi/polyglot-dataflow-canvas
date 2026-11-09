"""电路板信号灯流数据源 —— 每帧输出交通灯相位 + 8 路 LED 状态。

  · tick(frame, state, PARAMS)   每帧调用，帧号驱动红/黄/绿相位状态机
  · traffic_stream(PARAMS)       生成器写法，可切换入口函数体验

每路信号有 state(on/off/blink/fault) 与 voltage，前端 PCB 灯板据此点亮、闪烁或告警。
"""

import random


NAMES = ["PWR", "CLK", "TX", "RX", "BUS", "IO1", "IO2", "ALM"]


def _int(cfg, key, default):
    try:
        return int(cfg.get(key, default))
    except (TypeError, ValueError):
        return default


def _float(cfg, key, default):
    try:
        return float(cfg.get(key, default))
    except (TypeError, ValueError):
        return default


def tick(frame, state, PARAMS=None):
    """一帧信号快照：三色交通灯 + 多路 LED + 偶发故障。"""
    cfg = PARAMS or {}
    lanes = max(1, min(12, _int(cfg, "lanes", 8)))
    period = max(6, _int(cfg, "period", 12))
    fault_pct = _float(cfg, "fault_rate", 6.0)   # 面板填百分数，也兼容 0.06 写法
    fault_rate = max(0.0, min(0.5, fault_pct / 100.0 if fault_pct > 1 else fault_pct))
    seed = _int(cfg, "seed", 11)

    rng = random.Random(seed * 7919 + frame)
    phase = frame % period
    # 绿 → 黄 → 红 三段式相位（红绿灯状态机）
    green_end = max(2, int(period * 0.42))
    yellow_end = green_end + max(1, int(period * 0.17))
    light = "green" if phase < green_end else ("yellow" if phase < yellow_end else "red")

    prev = state.get("prev") or {}
    signals = []
    faults = 0
    for i in range(lanes):
        name = NAMES[i] if i < len(NAMES) else "IO%d" % i
        is_fault = rng.random() < fault_rate
        # 时钟常闪、总线随相位翻转、电源常亮、其余按帧号与随机数决定
        if name == "CLK":
            st = "blink"
        elif name == "PWR":
            st = "on"
        elif name == "BUS":
            st = "on" if light == "green" else "off"
        elif name == "ALM":
            st = "blink" if light == "red" else "off"
        else:
            st = "on" if (frame + i) % 3 == 0 or rng.random() > 0.55 else "off"
        if is_fault:
            st = "fault"
            faults += 1
        voltage = 0.0 if st in ("off", "fault") else (3.3 if st == "on" else 1.65)
        signals.append({
            "id": i,
            "name": name,
            "state": st,
            "voltage": voltage,
            "value": st in ("on", "blink"),
            "since": (prev.get(str(i), {}).get("since", frame)
                      if prev.get(str(i), {}).get("state") == st else frame),
        })

    state["prev"] = {str(s["id"]): {"state": s["state"], "since": s["since"]} for s in signals}
    state["cycles"] = frame // period

    return {
        "light": light,
        "lights": {k: (light == k) for k in ("red", "yellow", "green")},
        "phase": phase,
        "period": period,
        "cycle": frame // period,
        "remaining": period - phase,
        "signals": signals,
        "faults": faults,
        "frame": frame,
        "board_id": "CTRL-BOARD / REV.A",
    }


def traffic_stream(PARAMS=None):
    """生成器写法：帧号由本地计数器维护。"""
    state = {}
    frame = 0
    while True:
        frame += 1
        yield tick(frame, state, PARAMS)
