"""表格数据源：产出 Frame（无 pandas 依赖；装了 pandas 时真实 DataFrame 同样被识别）。"""


def execute(inputs):
    rows = int(PARAMS.get("rows", 4))
    ids = list(range(1, rows + 1))
    temps = [round(18.5 + i * 1.25, 2) for i in range(rows)]
    stations = ["东塔", "西塔", "南塔", "北塔", "中塔"]
    labels = [stations[i % len(stations)] for i in range(rows)]
    online = [i % 3 != 0 for i in range(rows)]

    table = Frame({
        "id": ids,
        "temperature": temps,
        "station": labels,
        "online": online,
    })
    return {"table": table}
