"""
scheduler.py — 拓扑调度与数据拼接

需求文档 3.3：
  - 按拓扑序执行模块
  - 每条边按两端 transport 决定怎么把数据从上游送到下游
  - memory_object -> bytes 且中间没有适配器时，调度中止并报「边 X 需要适配器模块」
  - format_hint 做简单字符串匹配的兼容性提示
"""

from __future__ import annotations

import fnmatch
import json
import os
import time
from collections import deque
from typing import Any

from .adapters import get_adapter, to_bytes
from .environments import INPROCESS_ID
from .core import (
    Edge,
    EdgeRequiresAdapter,
    ExecutionContext,
    ExecutionError,
    ExecutionResult,
    ModuleInstance,
    ModuleManifest,
    Port,
)
from .var_tree import build_tree


# --------------------------------------------------------------------------- #
# 格式兼容提示
# --------------------------------------------------------------------------- #

def check_format(up: Port, down: Port) -> tuple[bool, str]:
    """简单字符串匹配（下游 accepts 缺省时用自身 format_hint）。"""
    accepts = [a for a in (down.accepts or []) if a] or [down.format_hint]
    if not accepts or "any" in accepts or "*" in accepts or up.format_hint == "any":
        return True, "格式提示兼容（any / 未约束）"
    if up.format_hint in accepts:
        return True, f"格式提示匹配：{up.format_hint}"
    for pattern in accepts:
        if fnmatch.fnmatch(up.format_hint, pattern):
            return True, f"格式提示通配匹配：{up.format_hint} ~ {pattern}"
    return False, (
        f"上游 format_hint='{up.format_hint}' 不在下游可接受列表 {accepts} 中，"
        "建议插入适配器占位节点或人工确认"
    )


# --------------------------------------------------------------------------- #
# 传输规则
# --------------------------------------------------------------------------- #

def transfer(
    edge: Edge,
    up: Port,
    down: Port,
    value: Any,
    ctx: ExecutionContext,
    downstream_external: bool = False,
) -> tuple[Any, str]:
    """返回 (送达下游的值, 采用的规则描述)。"""
    u, d = up.transport, down.transport

    # 需求明确的中止条件：上游只能内存对象，下游却要求原始字节
    if u == "memory_object" and d == "bytes":
        raise EdgeRequiresAdapter(
            edge.id,
            f"{edge.src}.{up.name}({up.transport}/{up.format_hint}) → "
            f"{edge.dst}.{down.name}({down.transport}/{down.format_hint})；"
            "请插入一个输出为 bytes 的适配器模块",
        )

    # 同进程内两个 Python 内存对象：直接传引用，不序列化
    if u == "memory_object" and d == "memory_object":
        if downstream_external:
            return value, ("memory_object → memory_object：目标节点在外部 Python 环境，"
                           "由 runner 经 JSON + base64 桥接还原为原生对象后传入")
        return value, "memory_object → memory_object：同进程直接传对象（零拷贝引用）"

    # 下游要内存对象：上游载荷先转 bytes，再按 JSON 反序列化
    if d == "memory_object":
        raw = to_bytes(value)
        text = raw.decode("utf-8", errors="replace")
        try:
            return json.loads(text), f"{u} → memory_object：JSON 序列化后反序列化"
        except (json.JSONDecodeError, TypeError):
            return text, f"{u} → memory_object：非 JSON 载荷，按 UTF-8 文本传入"

    # 下游要字节
    if d == "bytes":
        return to_bytes(value), f"{u} → bytes：{'直传字节' if isinstance(value, bytes) else 'JSON 序列化 / UTF-8 编码'}"

    # 下游要文件路径
    if d == "file_path":
        raw = to_bytes(value)
        path = os.path.join(ctx.base_dir, "scripts", f"_wire_{ctx.run_id}_{edge.id}.bin")
        with open(path, "wb") as f:
            f.write(raw)
        ctx.temp_files.append(path)
        return path, f"{u} → file_path：落盘为 {os.path.basename(path)}，向下游传路径字符串"

    return value, f"{u} → {d}：原样传递（未识别组合）"


def _read_upstream(value: Any, up: Port) -> Any:
    """上游端口声明 file_path 时，值可能是路径，需要读回内容。"""
    if up.transport == "file_path" and isinstance(value, str) and os.path.isfile(value):
        with open(value, "rb") as f:
            return f.read()
    return value


# --------------------------------------------------------------------------- #
# 调度器
# --------------------------------------------------------------------------- #

class Scheduler:
    def __init__(self, base_dir: str, manifests: dict[str, ModuleManifest] | None = None):
        self.base_dir = base_dir
        self.manifests = manifests or {}

    def run(self, raw_nodes: list[dict], raw_edges: list[dict]) -> dict:
        started = time.perf_counter()
        ctx = ExecutionContext.create(self.base_dir)

        nodes: list[ModuleInstance] = [
            ModuleInstance.from_dict(n, self.manifests.get(n.get("manifest_id"))) for n in raw_nodes
        ]
        edges: list[Edge] = [Edge.from_dict(e) for e in raw_edges]

        # 实时视图节点只在浏览器中渲染帧，不参与一次性批处理执行。
        # 过滤掉它和它的入边，保证「运行数据流」仍按原来的拓扑语义工作。
        render_ids = {n.node_id for n in nodes
                      if n.stream_role == "render" or n.runtime == "render"}
        if render_ids:
            nodes = [n for n in nodes if n.node_id not in render_ids]
            edges = [e for e in edges if e.src not in render_ids and e.dst not in render_ids]

        by_id = {n.node_id: n for n in nodes}
        results: dict[str, ExecutionResult] = {}
        edge_log: list[dict] = []
        warnings: list[str] = []
        executed: list[str] = []
        fatal: str | None = None

        try:
            order = self._topo(nodes, edges)
        except ExecutionError as exc:
            return {
                "ok": False, "run_id": ctx.run_id, "order": [], "results": {},
                "edge_log": [], "warnings": [], "error": exc.message,
                "total_ms": round((time.perf_counter() - started) * 1000, 2),
            }

        for node_id in order:
            node = by_id[node_id]
            incoming = [e for e in edges if e.dst == node_id]
            inputs: dict[str, Any] = {}

            try:
                for edge in incoming:
                    src = by_id.get(edge.src)
                    up = src.port("outputs", edge.src_port) if src else None
                    down = node.port("inputs", edge.dst_port)
                    if up is None or down is None:
                        warnings.append(f"边 {edge.id} 引用了不存在的端口，已跳过")
                        continue

                    src_result = results.get(edge.src)
                    if src_result is None:
                        warnings.append(f"边 {edge.id} 的上游尚未产出结果，按 None 传入")
                        value = None
                    else:
                        value = _read_upstream(src_result.outputs.get(edge.src_port), up)

                    fmt_ok, fmt_msg = check_format(up, down)
                    external = (node.runtime == "python" and node.env_id not in ("", INPROCESS_ID))
                    payload, rule = transfer(edge, up, down, value, ctx, downstream_external=external)
                    inputs[edge.dst_port] = payload
                    if not fmt_ok and not edge.via_adapter:
                        warnings.append(f"边 {edge.id}：{fmt_msg}")
                    edge_log.append({
                        "id": edge.id,
                        "from": f"{edge.src}.{edge.src_port}",
                        "to": f"{edge.dst}.{edge.dst_port}",
                        "from_transport": up.transport,
                        "to_transport": down.transport,
                        "from_format_hint": up.format_hint,
                        "to_format_hint": down.format_hint,
                        "format_compatible": fmt_ok,
                        "format_message": fmt_msg,
                        "rule": rule,
                        "via_adapter": edge.via_adapter,
                    })
            except EdgeRequiresAdapter as exc:
                fatal = str(exc)
                break

            adapter = get_adapter(node.runtime)
            try:
                result = adapter.execute(node, inputs, ctx)
            except ExecutionError as exc:
                result = self._failed(node, exc)
            except Exception as exc:  # pragma: no cover - 兜底
                result = self._failed(node, ExecutionError(f"{type(exc).__name__}: {exc}"))
            results[node_id] = result
            executed.append(node_id)
            if result.error:
                warnings.append(f"模块 {node.name}（{node_id}）执行异常：{result.error}")

        payload = {
            "ok": fatal is None and all(r.error is None for r in results.values()),
            "run_id": ctx.run_id,
            "order": order,
            "executed": executed,
            "results": {k: v.to_dict() for k, v in results.items()},
            "edge_log": edge_log,
            "warnings": warnings,
            "error": fatal,
            "temp_files": [os.path.basename(p) for p in ctx.temp_files],
            "total_ms": round((time.perf_counter() - started) * 1000, 2),
        }
        return payload

    @staticmethod
    def _failed(node: ModuleInstance, exc: ExecutionError) -> ExecutionResult:
        return ExecutionResult(
            node_id=node.node_id,
            runtime_label=get_adapter(node.runtime).runtime_label,
            outputs={},
            variable_tree={"inputs": [], "outputs": [], "locals": []},
            meta={
                "exit_code": exc.exit_code,
                "duration_ms": 0,
                "runtime_label": get_adapter(node.runtime).runtime_label,
                "stderr_tail": exc.stderr_tail,
            },
            error=exc.message,
        )

    @staticmethod
    def _topo(nodes: list[ModuleInstance], edges: list[Edge]) -> list[str]:
        ids = [n.node_id for n in nodes]
        id_set = set(ids)
        indeg = {i: 0 for i in ids}
        adj: dict[str, list[str]] = {i: [] for i in ids}
        for e in edges:
            if e.src in id_set and e.dst in id_set:
                adj[e.src].append(e.dst)
                indeg[e.dst] += 1
        queue = deque(i for i in ids if indeg[i] == 0)
        order: list[str] = []
        while queue:
            u = queue.popleft()
            order.append(u)
            for v in adj[u]:
                indeg[v] -= 1
                if indeg[v] == 0:
                    queue.append(v)
        if len(order) != len(ids):
            stuck = [i for i in ids if i not in order]
            raise ExecutionError(f"画布存在环路，无法拓扑排序：{', '.join(stuck)}")
        return order
