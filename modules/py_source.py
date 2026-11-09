"""Python 数据源：产出原生 Python 对象，不做任何序列化。

约定入口 def execute(inputs)。PythonAdapter 会注入：
  - PARAMS：右侧参数面板里的配置
  - Frame：零依赖表格对象（用于演示变量树的列 dtype 分支）
"""

NAMES = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta"]


def execute(inputs):
    count = int(PARAMS.get("count", 5))
    label = str(PARAMS.get("label", "batch-A"))
    with_frame = bool(PARAMS.get("with_frame", True))

    records = []
    for i in range(count):
        records.append({
            "id": i + 1,
            "name": NAMES[i % len(NAMES)],
            "score": 60 + (i * 7) % 40,
            "active": i % 2 == 0,
        })

    # 验收点：list 容器 + 每个元素的 int / str 类型标注
    mixed = [1, "two", 3, "four", 5, "six", 7]

    frame = Frame({
        "id": [r["id"] for r in records],
        "score": [float(r["score"]) for r in records],
        "name": [r["name"] for r in records],
    })

    data = {
        "label": label,
        "count": count,
        "records": records,
        "mixed": mixed,
        "frame": frame if with_frame else None,
    }
    return {"data": data}
