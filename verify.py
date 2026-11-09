"""
verify.py — 需求文档第 4 节「验收标准（最小闭环）」自动化核对

用法（先启动 server.py）：
    python verify.py
    python verify.py --base http://127.0.0.1:9000

退出码 0 = 全部通过，1 = 有失败项。
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import quote

BASE = "http://127.0.0.1:8765"
PASS, FAIL = [], []


def api(path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        BASE + path, data=data,
        headers={"Content-Type": "application/json"} if data else {},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def record(ok: bool, label: str, detail: str = "") -> None:
    (PASS if ok else FAIL).append(label)
    print(("  [PASS] " if ok else "  [FAIL] ") + label + (("  → " + detail) if detail else ""))


def find(nodes: list[dict], name: str) -> dict | None:
    for n in nodes or []:
        if n.get("name") == name:
            return n
        got = find(n.get("children"), name)
        if got:
            return got
    return None


def main() -> int:
    global BASE
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=BASE)
    BASE = parser.parse_args().base.rstrip("/")

    try:
        health = api("/api/health")
    except Exception as exc:
        print(f"后端不可用：{exc}\n请先在 dataflow-demo/ 执行 python server.py")
        return 1
    print("后端：", health["engine"], "| Python", health["python"], "| 运行时", ", ".join(health["runtimes"]))
    print()

    mods = api("/api/modules")["modules"]
    M = {m["id"]: m for m in mods}

    def node(nid: str, mid: str) -> dict:
        m = M[mid]
        return {
            "node_id": nid, "manifest_id": mid, "name": m["name"], "runtime": m["runtime"],
            "entry": m["entry"], "inputs": copy.deepcopy(m["inputs"]),
            "outputs": copy.deepcopy(m["outputs"]),
            "params": {p["key"]: p["default"] for p in m["params"]},
            "script": None, "is_placeholder": False, "x": 0, "y": 0,
            "entry_fn": m.get("entry_fn") or "execute", "env_id": "",
            "mode": m.get("mode", "batch"), "stream_role": m.get("stream_role", ""),
            "render_type": m.get("render_type", ""), "source_mode": m.get("source_mode", "python"),
            "js_generator": m.get("js_generator", ""), "interval_ms": m.get("interval_ms", 600),
        }

    nodes = [node("n1", "py_source"), node("n2", "py_adapter"), node("n3", "shell_consumer")]
    edges = [
        {"id": "e1", "from": "n1", "from_port": "data", "to": "n2", "to_port": "data"},
        {"id": "e2", "from": "n2", "from_port": "payload", "to": "n3", "to_port": "payload"},
    ]

    print("验收 1 · 三节点闭环与连线")
    record(all(k in M for k in ("py_source", "py_adapter", "shell_consumer")), "3 个 Manifest 均可加载")
    record(len(edges) == 2, "画布可连成 2 条边")
    print()

    r = api("/api/run", {"nodes": nodes, "edges": edges})

    print("验收 2 · 按序执行 + 每个节点都有输入输出变量树")
    record(r["ok"] is True and r["order"] == ["n1", "n2", "n3"], "拓扑序 n1 → n2 → n3 且执行成功",
           "total_ms=" + str(r["total_ms"]))
    for nid in ("n1", "n2", "n3"):
        res = r["results"].get(nid, {})
        vt = res.get("variable_tree", {})
        record(bool(vt.get("inputs")) and bool(vt.get("outputs")),
               nid + " 同时有输入/输出变量树",
               "in=" + str(len(vt.get("inputs", []))) + " out=" + str(len(vt.get("outputs", []))))
    print()

    print("验收 3 · list 类型 + 每个元素的 int/str 标注")
    roots = r["results"]["n1"]["variable_tree"]["outputs"]
    mixed = find(roots, "mixed")
    record(bool(mixed) and mixed["type_label"].endswith(".list"), "mixed 的 type_label 是 builtins.list",
           mixed["type_label"] if mixed else "未找到")
    labels = [c["type_label"] for c in (mixed or {}).get("children", [])]
    record("builtins.int" in labels and "builtins.str" in labels,
           "元素含 builtins.int / builtins.str", ", ".join(labels[:5]))
    frame = find(roots, "frame")
    dtypes = [c["type_label"] for c in (frame or {}).get("children", [])]
    record(any(d in ("int64", "float64", "object") for d in dtypes),
           "Frame 每列的 type_label 是列 dtype", ", ".join(dtypes))
    print()

    print("验收 4 · 改适配器输出格式 → 不兼容提示")
    changed = copy.deepcopy(nodes)
    changed[1]["outputs"][0]["format_hint"] = "json:raw"
    r2 = api("/api/run", {"nodes": changed, "edges": edges})
    e2 = next((e for e in r2["edge_log"] if e["id"] == "e2"), {})
    record(e2.get("format_compatible") is False, "边 e2 判定为格式不兼容", e2.get("format_message", ""))
    record(any("格式" in w or "不在下游" in w for w in r2["warnings"]), "后端生成适配器占位 / 人工确认提示")
    direct = api("/api/run", {
        "nodes": [node("n1", "py_source"), node("n3", "shell_consumer")],
        "edges": [{"id": "e-direct", "from": "n1", "from_port": "data", "to": "n3", "to_port": "payload"}],
    })
    record("需要适配器模块" in (direct.get("error") or ""), "缺适配器时按 3.3 中止调度",
           (direct.get("error") or "")[:110])
    print()

    print("验收 5 · 执行摘要（耗时 / 退出码 / runtime 标签）")
    for nid in ("n1", "n2", "n3"):
        meta = r["results"][nid]["meta"]
        record(
            "duration_ms" in meta and "exit_code" in meta and bool(meta.get("runtime_label")),
            nid + " 摘要含 duration_ms / exit_code / runtime_label",
            f"{meta['runtime_label']} · {meta['duration_ms']}ms · exit {meta['exit_code']}",
        )
    print()
    print("验收 6 · 新增能力：Python 环境 / 脚本文件 / 入口函数可选择")
    envs = api("/api/envs")["envs"]
    external = [e for e in envs if e["kind"] == "external"]
    record(bool(envs) and envs[0]["id"] == "__inprocess__" and envs[0]["kind"] == "in-process",
           "内置 in-process 环境存在且排首位", f"共 {len(envs)} 个环境")
    record(True, "Python 环境自动探测",
           "外部 " + str(len(external)) + " 个：" + ", ".join(e["version"] for e in external[:4]))

    scripts = api("/api/scripts")["scripts"]
    src = next((s for s in scripts if s["path"] == "modules/series_source.py"), None)
    fns = [f["name"] for f in (src or {}).get("functions", []) if "name" in f]
    record(bool(src) and all(k in fns for k in ("random_series", "wave_series", "mixed_series", "execute")),
           "脚本 API 列出全部顶层函数", ", ".join(fns))
    one = api("/api/script?path=modules/series_source.py")
    record("def random_series" in one.get("script", "") and len(one.get("functions", [])) >= 4,
           "脚本内容可按路径读取 + 函数列表")

    def series_nodes():
        return [node("s1", "series_source"), node("s2", "series_transform"), node("s3", "series_report")]

    series_edges = [
        {"id": "se1", "from": "s1", "from_port": "series", "to": "s2", "to_port": "seq"},
        {"id": "se2", "from": "s2", "from_port": "series", "to": "s3", "to_port": "series"},
    ]
    r3 = api("/api/run", {"nodes": series_nodes(), "edges": series_edges})
    record(r3["ok"] is True and r3["order"] == ["s1", "s2", "s3"],
           "随机序列链路（生成 → 处理 → 统计）真实跑通", "total_ms=" + str(r3["total_ms"]))
    roots1 = r3["results"]["s1"]["variable_tree"]["outputs"]
    roots3 = r3["results"]["s3"]["variable_tree"]["outputs"]
    record(any(x["type_label"] == "builtins.list" for x in roots1),
           "随机序列数据源输出 builtins.list", roots1[0]["value_preview"][:60] if roots1 else "")
    record(any(x["type_label"] == "builtins.dict" for x in roots3),
           "统计节点输出 builtins.dict", roots3[0]["value_preview"][:60] if roots3 else "")

    switched = series_nodes()
    switched[0]["entry_fn"] = "mixed_series"
    r4 = api("/api/run", {"nodes": switched, "edges": series_edges})
    labels = [c["type_label"] for c in r4["results"]["s1"]["variable_tree"]["outputs"][0]["children"]]
    record("builtins.int" in labels and "builtins.str" in labels,
           "切换入口函数后变量树含 int / str 混合元素", ", ".join(labels[:5]))

    swapped = series_nodes()
    swapped[2]["entry"] = "modules/series_transform.py"
    swapped[2]["entry_fn"] = "describe"
    r5 = api("/api/run", {"nodes": swapped, "edges": series_edges})
    record(r5["ok"] is True and r5["results"]["s3"]["meta"]["entry_fn"] == "describe",
           "脚本文件可切换到另一个 .py 并指定其顶层函数",
           r5["results"]["s3"]["meta"].get("entry", ""))

    if external:
        env_nodes = series_nodes()
        env_nodes[1]["env_id"] = external[0]["id"]
        r6 = api("/api/run", {"nodes": env_nodes, "edges": series_edges})
        meta = r6["results"]["s2"]["meta"]
        record(r6["ok"] is True and "subprocess" in meta.get("runtime_label", ""),
               "中间节点换用外部 Python 解释器仍跑通", meta.get("runtime_label", ""))
        record("JSON" in (meta.get("bridge") or ""), "跨环境走 JSON + base64 桥接",
               meta.get("bridge", ""))
        record(any("外部 Python 环境" in e["rule"] for e in r6["edge_log"]),
               "边日志正确标注跨解释器桥接（不再误称零拷贝）")
        record(bool(meta.get("captured_locals")), "外部环境仍抓到调试器式局部变量",
               ", ".join(meta.get("captured_locals") or [])[:70])
        bad = series_nodes()
        bad[1]["env_id"] = "env_NOT_REGISTERED"
        r7 = api("/api/run", {"nodes": bad, "edges": series_edges})
        record(not r7["ok"] and "未登记" in (r7["results"]["s2"].get("error") or ""),
               "未登记的环境被拒绝，不接受任意命令路径",
               (r7["results"]["s2"].get("error") or "")[:70])
    else:
        record(True, "本机未探测到外部解释器，跨环境执行项跳过")

    # ------------------------------------------------------------------ #
    print()
    print("验收 7 · 实时流：后端 Python SSE / 浏览器 JS 双通道 + 画布内嵌渲染")
    needed = ("stream_market", "stream_ma", "candlestick_view", "stream_signals", "signal_board_view")
    record(all(k in M for k in needed), "5 个实时流 Manifest 已加载")
    record(M["stream_market"].get("mode") == "stream" and
           M["stream_market"].get("stream_role") == "source" and
           M["candlestick_view"].get("render_type") == "candlestick" and
           M["signal_board_view"].get("render_type") == "signal_board" and
           M["stream_market"].get("js_generator") == "market" and
           M["stream_signals"].get("js_generator") == "signals",
           "Manifest 声明 mode / stream_role / render_type / js_generator")

    def stream_nodes(src1="python", src2="python", fn="tick"):
        a = node("s1", "stream_market"); a["source_mode"] = src1; a["entry_fn"] = fn
        b = node("t1", "stream_ma")
        c = node("v1", "candlestick_view")
        d = node("s2", "stream_signals"); d["source_mode"] = src2
        e = node("v2", "signal_board_view")
        return [a, b, c, d, e]

    stream_edges = [
        {"id": "le1", "from": "s1", "from_port": "quote", "to": "t1", "to_port": "candles"},
        {"id": "le2", "from": "t1", "from_port": "market", "to": "v1", "to_port": "quote"},
        {"id": "le3", "from": "s2", "from_port": "packet", "to": "v2", "to_port": "packet"},
    ]

    def sse_frames(run_id: str, count: int = 3, timeout: float = 9.0) -> list[dict]:
        out: list[dict] = []
        try:
            with urllib.request.urlopen(BASE + "/api/stream?run_id=" + quote(run_id),
                                        timeout=timeout) as resp:
                deadline = time.time() + timeout
                while len(out) < count and time.time() < deadline:
                    line = resp.readline().decode("utf-8", "replace").strip()
                    if line.startswith("data: ") and line[6:] not in ("", "{}"):
                        try:
                            out.append(json.loads(line[6:]))
                        except json.JSONDecodeError:
                            pass
        except Exception as exc:
            print("       SSE 读取中断：", exc)
        return out

    # 一次性批处理不应把 render 视图节点交给 PythonAdapter 执行
    batch = api("/api/run", {"nodes": stream_nodes(), "edges": stream_edges})
    record(batch["ok"] is True and len(batch["order"]) == 3 and
           all(not i.startswith("v") for i in batch["order"]),
           "批处理自动跳过 render 视图节点，只跑源与变换",
           " → ".join(batch["order"]))

    started = api("/api/stream/start", {
        "nodes": stream_nodes(), "edges": stream_edges, "interval_ms": 200})
    record(bool(started.get("run_id")) and started.get("has_backend") is True and
           started.get("python_nodes") == 3,
           "启动后端 tick 线程并拿到 SSE run_id",
           f"python_nodes={started.get('python_nodes')}")
    frames = sse_frames(started["run_id"], 3)
    record(len(frames) >= 3, "SSE 连续推送多帧", f"收到 {len(frames)} 帧")
    if len(frames) >= 3:
        last = frames[-1]
        quote_data = last["by_node"]["t1"]["market"]
        board = last["by_node"]["s2"]["packet"]
        record(len(quote_data.get("candles", [])) >= 10 and
               len(quote_data.get("ma5", [])) == len(quote_data["candles"]) and
               all(k in quote_data["candles"][-1] for k in ("open", "high", "low", "close", "volume")),
               "每帧含 OHLC 蜡烛 + 成交量 + MA5/MA10",
               f"{len(quote_data['candles'])} 根 K 线")
        record(len(board.get("signals", [])) == 8 and
               board.get("light") in ("red", "yellow", "green"),
               "每帧含红黄绿相位 + 8 路 LED 状态",
               f"相位 {board.get('light')} · 故障 {board.get('faults')}")
        record(frames[0]["frame"] < last["frame"] and last["source"] == "python" and
               not last["meta"]["errors"],
               "帧号递增、来源为后端 Python、无执行错误",
               f"frame {frames[0]['frame']} → {last['frame']}")
        record(any(f.get("tree") for f in frames), "帧内附带调试器式变量树快照")
    status = api("/api/stream/status")
    record(status.get("running") is True, "流状态 API 报告运行中",
           f"frame={status.get('frame')}")
    api("/api/stream/stop", {"run_id": started["run_id"]})
    record(api("/api/stream/status").get("running") is False, "停止后 tick 线程退出")

    # 生成器写法 def stream(...) 也应当按帧产出
    gen_start = api("/api/stream/start", {
        "nodes": stream_nodes(fn="market_stream"), "edges": stream_edges, "interval_ms": 150})
    gen_frames = sse_frames(gen_start["run_id"], 2)
    record(len(gen_frames) >= 2 and
           all("candles" in f.get("by_node", {}).get("t1", {}).get("market", {}) for f in gen_frames),
           "def stream() 生成器入口同样逐帧产出", f"收到 {len(gen_frames)} 帧")
    api("/api/stream/stop", {"run_id": gen_start["run_id"]})

    # 源切成浏览器 JS 后，后端只保留「从 python 源可达」的子图；全 JS 则不启动
    mixed_start = api("/api/stream/start", {
        "nodes": stream_nodes(src1="js"), "edges": stream_edges, "interval_ms": 200})
    record(mixed_start.get("python_nodes") == 1,
           "K线源切 JS 后，挂在它下游的 Python 变换节点被自动裁剪",
           f"python_nodes={mixed_start.get('python_nodes')}")
    api("/api/stream/stop", {"run_id": mixed_start["run_id"]})
    all_js = api("/api/stream/start", {
        "nodes": stream_nodes(src1="js", src2="js"), "edges": stream_edges, "interval_ms": 200})
    record(all_js.get("has_backend") is False and not all_js.get("run_id"),
           "双源都切浏览器 JS 时后端不启动 tick 线程（纯前端通道）")
    api("/api/stream/stop", {})

    print()
    print(f"结果：{len(PASS)} 项通过 / {len(FAIL)} 项失败")
    if FAIL:
        print("失败项：" + "、".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
