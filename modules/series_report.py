"""序列统计 / 消费者 —— 第三个 Python 节点，用来演示节点各自选环境与入口函数。"""
import statistics


def _numbers(seq):
    return [float(x) for x in seq if isinstance(x, (int, float)) and not isinstance(x, bool)]


def summarize(series):
    """汇总统计，形参名 series 与输入端口同名，会自动接到上游 list"""
    values = _numbers(series)
    stats = {
        "count": len(series),
        "numeric": len(values),
        "min": min(values) if values else None,
        "max": max(values) if values else None,
        "mean": round(statistics.fmean(values), 4) if values else None,
    }
    return {"report": stats}


def histogram(series, bins=5):
    """简单分桶直方图"""
    values = _numbers(series)
    if not values:
        return {"report": {"bins": int(bins), "counts": [], "edges": []}}
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    size = max(1, int(bins))
    counts = [0] * size
    for v in values:
        idx = min(size - 1, int((v - lo) / span * size))
        counts[idx] += 1
    edges = [round(lo + span * i / size, 4) for i in range(size + 1)]
    return {"report": {"bins": size, "counts": counts, "edges": edges}}


def execute(inputs):
    """兼容约定入口"""
    seq = next(iter(inputs.values()), [])
    return summarize(seq if isinstance(seq, (list, tuple)) else [seq])
