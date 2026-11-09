"""JSON 扁平化适配器：全程 memory_object -> memory_object，调度器零拷贝直接传引用。"""


def execute(inputs):
    src = inputs.get("data")
    if not isinstance(src, dict):
        src = {"value": src}
    prefix = str(PARAMS.get("prefix", "item"))

    records = src.get("records")
    if not isinstance(records, list):
        records = [src]

    flat = {}
    for index, row in enumerate(records):
        if not isinstance(row, dict):
            flat["%s.%d" % (prefix, index)] = row
            continue
        for key, value in row.items():
            flat["%s.%d.%s" % (prefix, index, key)] = value

    meta = {"row_count": len(records), "key_count": len(flat), "prefix": prefix}
    result = {"_meta": meta, **flat}
    return {"flat": result}
