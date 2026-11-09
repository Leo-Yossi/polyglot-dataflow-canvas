"""序列处理器 —— 同样可以任选顶层函数作为入口。

参数注入示例：
  def scale(seq, factor=2) 中的 seq 会拿到上游端口的值，factor 来自参数面板
  def describe(seq) 返回普通 dict，会被自动归到唯一的输出端口 series
"""


def _numbers(seq):
    return [float(x) for x in seq if isinstance(x, (int, float)) and not isinstance(x, bool)]


def scale(seq, factor=2, bias=0):
    """线性缩放：y = x * factor + bias"""
    out = [round(float(x) * float(factor) + float(bias), 4) for x in _numbers(seq)]
    return out


def normalize(seq):
    """归一化到 [0, 1]"""
    values = _numbers(seq)
    lo, hi = (min(values), max(values)) if values else (0.0, 1.0)
    span = (hi - lo) or 1.0
    normalized = [round((v - lo) / span, 4) for v in values]
    return normalized


def moving_average(seq, window=3):
    """滑动平均"""
    values = _numbers(seq)
    size = max(1, int(window))
    averaged = []
    for i in range(len(values)):
        chunk = values[max(0, i - size + 1): i + 1]
        averaged.append(round(sum(chunk) / len(chunk), 4))
    return averaged


def describe(seq):
    """返回统计 dict（不含端口名，由引擎自动包进 series 端口）"""
    values = _numbers(seq)
    return {
        "count": len(seq),
        "numeric": len(values),
        "total": round(sum(values), 4),
        "min": min(values) if values else None,
        "max": max(values) if values else None,
    }


def execute(inputs):
    """兼容约定入口"""
    seq = next(iter(inputs.values()), [])
    return scale(seq, factor=PARAMS.get("factor", 2), bias=PARAMS.get("bias", 0))
