"""实时流上的变换节点 —— 计算 MA5 / MA10 并并回行情包。

这演示「实时数据也能经过模块拼接」：K 线数据源 → 均线变换 → 渲染节点。
流引擎按输入端口名把上游的 candles 字段直接注入到这里的 candles 形参。
"""


def _int(cfg, key, default):
    try:
        return int(cfg.get(key, default))
    except (TypeError, ValueError):
        return default


def _ma(closes, n):
    out = []
    for i in range(len(closes)):
        if i + 1 < n:
            out.append(None)
        else:
            out.append(round(sum(closes[i + 1 - n:i + 1]) / n, 2))
    return out


def tick(candles, frame, PARAMS=None):
    """一帧变换：附加两条均线，其余字段透传给渲染节点。"""
    cfg = PARAMS or {}
    short = max(2, _int(cfg, "ma_short", 5))
    long = max(short + 1, _int(cfg, "ma_long", 10))

    if isinstance(candles, dict):
        payload = dict(candles)
        rows = list(payload.get("candles") or [])
    else:
        # 引擎按端口名把上游的 candles 字段拆包注入时，这里补回展示字段
        rows = list(candles or [])
        payload = {}
        if rows:
            last, first = rows[-1], rows[0]
            try:
                payload["change_pct"] = round(
                    (float(last["close"]) - float(first["open"])) / float(first["open"]) * 100, 2)
            except (KeyError, TypeError, ValueError, ZeroDivisionError):
                payload["change_pct"] = 0.0
            payload["last"] = last
            payload["price"] = last.get("close")
            payload["volume"] = last.get("volume", 0)
            payload["symbol"] = "DEMO·600519"

    closes = []
    for bar in rows:
        try:
            closes.append(float(bar["close"]))
        except (KeyError, TypeError, ValueError):
            closes.append(0.0)

    payload["candles"] = rows
    payload["ma5"] = _ma(closes, short)
    payload["ma10"] = _ma(closes, long)
    payload["ma_short"] = short
    payload["ma_long"] = long
    payload["frame"] = frame
    payload["transform"] = "stream_ma.tick"
    return payload


def execute(inputs):
    """兼容批处理约定写法，方便把本模块拖到普通画布里跑一次单帧。"""
    raw = next(iter(inputs.values()), {})
    return tick(raw, 0, {})
