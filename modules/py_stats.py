"""统计模块：展示 memory_object -> memory_object 的链路与局部变量抓取。"""


def execute(inputs):
    src = inputs.get("data")
    if not isinstance(src, dict):
        src = {"records": []}
    field = str(PARAMS.get("field", "score"))
    records = src.get("records") or []

    values = []
    for row in records:
        if isinstance(row, dict) and isinstance(row.get(field), (int, float)):
            values.append(row[field])

    if values:
        summary = {
            "field": field,
            "count": len(values),
            "sum": sum(values),
            "min": min(values),
            "max": max(values),
            "avg": round(sum(values) / len(values), 2),
            "types": sorted({type(v).__name__ for v in values}),
        }
    else:
        summary = {"field": field, "count": 0, "note": "无可统计的数值字段"}

    return {"stats": summary}
