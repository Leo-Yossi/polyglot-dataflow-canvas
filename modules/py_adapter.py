"""一等公民适配器模块：memory_object -> bytes。

关键点：输出端口声明为 transport=bytes，所以调度引擎允许它把数据
送给要求 bytes 的 Shell 消费者；如果直接把 py_source 连到 shell_consumer，
调度器会按需求 3.3 中止并提示「边 X 需要适配器模块」。
"""

import json


def execute(inputs):
    src = inputs.get("data")
    if isinstance(src, bytes):
        src = src.decode("utf-8", errors="replace")
    if isinstance(src, str):
        try:
            src = json.loads(src)
        except Exception:
            src = {"lines": src.splitlines()}
    if not isinstance(src, dict):
        src = {"value": src}

    delim = str(PARAMS.get("delimiter", "\t"))
    if delim == "\\t":
        delim = "\t"
    include_header = bool(PARAMS.get("include_header", True))

    records = src.get("records")
    if not isinstance(records, list):
        records = [src]

    lines = []
    if include_header:
        lines.append(delim.join(["id", "name", "score", "active"]))
    for row in records:
        if isinstance(row, dict):
            lines.append(delim.join([
                str(row.get("id", "")),
                str(row.get("name", "")),
                str(row.get("score", "")),
                str(row.get("active", "")),
            ]))
        else:
            lines.append(str(row))

    text = "\n".join(lines) + "\n"
    raw = text.encode("utf-8")
    return {"payload": raw}
