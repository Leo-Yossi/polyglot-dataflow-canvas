"""实时行情流数据源 —— 每帧随机游走生成一根 OHLC K 线。

两种入口写法都可用（在节点面板「入口函数」下拉里切换）：
  · tick(frame, state, PARAMS)          每帧调用，state 由引擎跨帧维护
  · market_stream(PARAMS)               生成器，自己保存跨帧状态，每帧 yield 一包

返回 dict 会被平台按输出端口名组织：这里只有一个输出端口 quote，
所以整包数据会成为 quote 端口的值，下游渲染节点直接取用。
"""

import random


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
    """生成一帧：最近 window 根 K 线 + 最新价 + 涨跌幅 + 成交量。"""
    cfg = PARAMS or {}
    window = max(8, _int(cfg, "window", 36))
    start = _float(cfg, "start_price", 100.0)
    volatility = max(0.1, _float(cfg, "volatility", 2.0))
    seed = _int(cfg, "seed", 7)

    candles = list(state.get("candles") or [])
    price = float(state.get("price") or start)
    rng = random.Random(seed * 100003 + frame)

    # 首帧先补一段历史，让 K 线一上来就有形态可看
    if not candles:
        p = start
        for i in range(min(14, window)):
            o = p
            c = max(1.0, o + rng.gauss(0.05, volatility * 0.7))
            candles.append({
                "t": i - min(14, window),
                "open": round(o, 2),
                "high": round(max(o, c) + rng.uniform(0, volatility * 0.6), 2),
                "low": round(min(o, c) - rng.uniform(0, volatility * 0.6), 2),
                "close": round(c, 2),
                "volume": rng.randint(700, 2600),
            })
            p = c
        price = p

    open_ = price
    close = max(1.0, open_ + rng.gauss(rng.uniform(-0.35, 0.4), volatility))
    high = max(open_, close) + rng.uniform(0.05, volatility * 0.9)
    low = min(open_, close) - rng.uniform(0.05, volatility * 0.9)
    volume = rng.randint(600, 2400)
    if abs(close - open_) > volatility * 1.1:
        volume = int(volume * 1.9)                 # 大波动放量

    bar = {
        "t": frame,
        "open": round(open_, 2),
        "high": round(high, 2),
        "low": round(low, 2),
        "close": round(close, 2),
        "volume": volume,
    }
    candles.append(bar)
    if len(candles) > window:
        candles = candles[-window:]

    state["candles"] = candles
    state["price"] = close
    first_open = candles[0]["open"] or 1.0
    change_pct = round((close - first_open) / first_open * 100, 2)

    return {
        "candles": candles,
        "last": bar,
        "price": round(close, 2),
        "change_pct": change_pct,
        "volume": volume,
        "frame": frame,
        "symbol": "DEMO·600519",
    }


def market_stream(PARAMS=None):
    """生成器写法：状态留在闭包里，引擎每帧 next() 取一包数据。"""
    state = {}
    frame = 0
    while True:
        frame += 1
        yield tick(frame, state, PARAMS)
