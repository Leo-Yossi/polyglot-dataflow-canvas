"""
core.py — 平台核心数据结构

对应需求文档 2. 核心概念 / 3.2 执行适配器接口 / 3.3 数据拼接规则。
所有对象都保持「序列化友好」：内部保存原生 Python 对象，对外只导出变量树与摘要。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

TRANSPORTS = ("memory_object", "bytes", "file_path")
RUNTIMES = ("python", "shell", "mcp", "http", "render")
STREAM_ROLES = ("", "source", "transform", "render")
RENDER_TYPES = ("", "candlestick", "signal_board")


class EdgeRequiresAdapter(Exception):
    """上游只能 memory_object、下游要求 bytes —— 必须通过适配器模块桥接。"""

    def __init__(self, edge_id: str, detail: str = ""):
        self.edge_id = edge_id
        self.detail = detail
        super().__init__(f"边 {edge_id} 需要适配器模块{('：' + detail) if detail else ''}")


class ExecutionError(Exception):
    """单个模块执行失败。"""

    def __init__(self, message: str, stderr_tail: str = "", exit_code: int = 1):
        super().__init__(message)
        self.message = message
        self.stderr_tail = stderr_tail
        self.exit_code = exit_code


# --------------------------------------------------------------------------- #
# Manifest
# --------------------------------------------------------------------------- #

@dataclass
class Port:
    name: str
    transport: str = "memory_object"
    format_hint: str = "any"
    accepts: list[str] = field(default_factory=list)
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "transport": self.transport,
            "format_hint": self.format_hint,
            "accepts": list(self.accepts),
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Port":
        return cls(
            name=str(d.get("name", "port")),
            transport=str(d.get("transport", "memory_object")),
            format_hint=str(d.get("format_hint", "any")),
            accepts=list(d.get("accepts") or []),
            description=str(d.get("description", "")),
        )


@dataclass
class ParamSpec:
    key: str
    type: str = "string"
    default: Any = None
    label: str = ""
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "type": self.type,
            "default": self.default,
            "label": self.label or self.key,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ParamSpec":
        return cls(
            key=str(d.get("key", "")),
            type=str(d.get("type", "string")),
            default=d.get("default"),
            label=str(d.get("label", "")),
            description=str(d.get("description", "")),
        )


@dataclass
class ModuleManifest:
    id: str
    name: str
    runtime: str
    entry: str
    category: str = "未分类"
    description: str = ""
    entry_fn: str = ""            # Manifest 可声明默认入口函数
    inputs: list[Port] = field(default_factory=list)
    outputs: list[Port] = field(default_factory=list)
    params: list[ParamSpec] = field(default_factory=list)

    # 实时流扩展（缺省 batch，对原批处理链路零影响）
    mode: str = "batch"              # batch | stream
    stream_role: str = ""            # source | transform | render
    render_type: str = ""            # candlestick | signal_board
    source_mode: str = "python"      # python（后端 SSE） | js（浏览器本地）
    js_generator: str = ""           # web/streams.js 里注册的 JS 生成器 key
    interval_ms: int = 600

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "runtime": self.runtime,
            "entry": self.entry,
            "category": self.category,
            "description": self.description,
            "entry_fn": self.entry_fn,
            "inputs": [p.to_dict() for p in self.inputs],
            "outputs": [p.to_dict() for p in self.outputs],
            "params": [p.to_dict() for p in self.params],
            "mode": self.mode,
            "stream_role": self.stream_role,
            "render_type": self.render_type,
            "source_mode": self.source_mode,
            "js_generator": self.js_generator,
            "interval_ms": self.interval_ms,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ModuleManifest":
        return cls(
            id=str(d["id"]),
            name=str(d.get("name", d["id"])),
            runtime=str(d.get("runtime", "python")),
            entry=str(d.get("entry", "")),
            category=str(d.get("category", "未分类")),
            description=str(d.get("description", "")),
            entry_fn=str(d.get("entry_fn", "")),
            inputs=[Port.from_dict(x) for x in (d.get("inputs") or [])],
            outputs=[Port.from_dict(x) for x in (d.get("outputs") or [])],
            params=[ParamSpec.from_dict(x) for x in (d.get("params") or [])],
            mode=str(d.get("mode", "batch")),
            stream_role=str(d.get("stream_role", "")),
            render_type=str(d.get("render_type", "")),
            source_mode=str(d.get("source_mode", "python")),
            js_generator=str(d.get("js_generator", "")),
            interval_ms=int(d.get("interval_ms") or 600),
        )


# --------------------------------------------------------------------------- #
# 实例（画布上的节点）
# --------------------------------------------------------------------------- #

@dataclass
class ModuleInstance:
    """画布上的一个节点实例。前端传来什么就以什么为准（支持改 format_hint / 内联脚本）。"""

    node_id: str
    manifest_id: str
    name: str
    runtime: str
    entry: str
    inputs: list[Port] = field(default_factory=list)
    outputs: list[Port] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)
    script: str | None = None
    is_placeholder: bool = False
    env_id: str = ""              # 空 / __inprocess__ = 服务内置解释器；其余为已登记的外部 env id
    entry_fn: str = ""            # 用户脚本里任选的入口函数；空 = execute 或唯一顶层函数
    label: str = ""
    x: float = 0
    y: float = 0
    mode: str = "batch"
    stream_role: str = ""
    render_type: str = ""
    source_mode: str = "python"
    js_generator: str = ""
    interval_ms: int = 600

    @classmethod
    def from_dict(cls, d: dict, manifest: ModuleManifest | None = None) -> "ModuleInstance":
        """前端节点 JSON -> 实例。manifest 用于补齐默认值。"""
        src_inputs = d.get("inputs")
        src_outputs = d.get("outputs")
        if manifest is not None:
            src_inputs = src_inputs if src_inputs is not None else [p.to_dict() for p in manifest.inputs]
            src_outputs = src_outputs if src_outputs is not None else [p.to_dict() for p in manifest.outputs]
        return cls(
            node_id=str(d["node_id"]),
            manifest_id=str(d.get("manifest_id", manifest.id if manifest else d["node_id"])),
            name=str(d.get("name", manifest.name if manifest else d["node_id"])),
            runtime=str(d.get("runtime", manifest.runtime if manifest else "python")),
            entry=str(d.get("entry", manifest.entry if manifest else "")),
            inputs=[Port.from_dict(x) for x in (src_inputs or [])],
            outputs=[Port.from_dict(x) for x in (src_outputs or [])],
            params=dict(d.get("params") or {}),
            script=d.get("script") or None,
            is_placeholder=bool(d.get("is_placeholder", False)),
            env_id=str(d.get("env_id") or ""),
            entry_fn=str(d.get("entry_fn") or (manifest.entry_fn if manifest else "")),
            label=str(d.get("label", "")),
            x=float(d.get("x", 0)),
            y=float(d.get("y", 0)),
            mode=str(d.get("mode") or (manifest.mode if manifest else "batch")),
            stream_role=str(d.get("stream_role") or (manifest.stream_role if manifest else "")),
            render_type=str(d.get("render_type") or (manifest.render_type if manifest else "")),
            source_mode=str(d.get("source_mode") or "python"),
            js_generator=str(d.get("js_generator") or (manifest.js_generator if manifest else "")),
            interval_ms=int(d.get("interval_ms") or (manifest.interval_ms if manifest else 600)),
        )

    def port(self, side: str, name: str) -> Port | None:
        seq = self.inputs if side == "inputs" else self.outputs
        for p in seq:
            if p.name == name:
                return p
        return None


@dataclass
class Edge:
    id: str
    src: str
    src_port: str
    dst: str
    dst_port: str
    via_adapter: str | None = None  # 若前端已插入占位节点，这里记其 node_id

    @classmethod
    def from_dict(cls, d: dict) -> "Edge":
        return cls(
            id=str(d.get("id") or uuid.uuid4().hex[:8]),
            src=str(d["from"]),
            src_port=str(d["from_port"]),
            dst=str(d["to"]),
            dst_port=str(d["to_port"]),
            via_adapter=d.get("via_adapter"),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "from": self.src,
            "from_port": self.src_port,
            "to": self.dst,
            "to_port": self.dst_port,
            "via_adapter": self.via_adapter,
        }


# --------------------------------------------------------------------------- #
# 执行上下文与结果
# --------------------------------------------------------------------------- #

@dataclass
class ExecutionContext:
    run_id: str
    base_dir: str
    started_at: float = field(default_factory=time.time)
    temp_files: list[str] = field(default_factory=list)

    @classmethod
    def create(cls, base_dir: str) -> "ExecutionContext":
        return cls(run_id=uuid.uuid4().hex[:12], base_dir=base_dir)

    def elapsed_ms(self) -> float:
        return round((time.time() - self.started_at) * 1000, 2)


@dataclass
class ExecutionResult:
    node_id: str
    runtime_label: str
    outputs: dict[str, Any] = field(default_factory=dict)
    variable_tree: dict = field(default_factory=lambda: {"inputs": [], "outputs": [], "locals": []})
    meta: dict = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "runtime_label": self.runtime_label,
            "variable_tree": self.variable_tree,
            "meta": self.meta,
            "error": self.error,
            "outputs_preview": {
                k: (f"<bytes {len(v)} B>" if isinstance(v, (bytes, bytearray)) else repr(v)[:160])
                for k, v in self.outputs.items()
            },
        }
