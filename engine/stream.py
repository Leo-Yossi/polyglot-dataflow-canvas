"""
stream.py — 实时流式执行引擎（SSE 后端通道）

与 Scheduler 的一次性批处理不同，这里每一帧都要跑一遍流子图：

  ┌ 后台 tick 线程 ─────────────────────────────────────────────┐
  │ 按拓扑序执行 mode=stream 的 python source / transform 节点    │
  │   · def tick(frame, state, PARAMS, <端口名>...) -> dict       │
  │   · def stream(...) -> generator（yield 一帧，跨帧状态自带）  │
  │   · def execute(inputs) 约定同样兼容                         │
  │ 帧 = {frame, ts, source, root, by_node, payload, tree, meta}  │
  │ 广播给所有 SSE 订阅者（每个订阅者一个带上限的队列）           │
  └──────────────────────────────────────────────────────────────┘

流链路上的 transport 统一为 memory_object：帧是同进程内的原生 Python 对象，
直传引用；只有发往浏览器的那一步才做 JSON 化（payload.jsonable）。
render 节点不在这里执行，它只在浏览器里根据帧重绘。
"""

from __future__ import annotations

import contextlib
import io
import os
import queue
import threading
import time
import traceback
import uuid
from typing import Any

from .core import Edge, ModuleInstance
from .entry import (
    build_call,
    call_and_capture_locals,
    clean_locals,
    find_entry,
    normalize_outputs,
)
from .payload import jsonable
from .var_tree import Frame, build_tree

TREE_EVERY = 3          # 每 N 帧附带一次完整变量树快照，其余帧复用前端上一份
QUEUE_MAX = 6           # 单个 SSE 订阅者最多积压的帧数，超出丢旧帧保实时性
IDLE_TIMEOUT = 20.0     # 连续无 SSE 订阅者超过该秒数自动收掉 tick 线程


class StreamJob:
    """一条正在推送的实时流。一个画布同时只跑一个 job，由 StreamHub 管理。"""

    def __init__(self, base_dir: str, raw_nodes: list[dict], raw_edges: list[dict],
                 interval_ms: int = 600, manifests: dict | None = None):
        self.run_id = uuid.uuid4().hex[:12]
        self.base_dir = base_dir
        self.interval_ms = max(80, min(10000, int(interval_ms or 600)))
        self.started_at = time.time()
        self.frame_no = 0
        self.fps = 0.0
        self.last_error: str | None = None
        self.finished = False

        self._subscribers: list[queue.Queue] = []
        self._sub_lock = threading.Lock()
        self._last_sub_ts = time.time()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._frame_times: list[float] = []

        all_nodes = [
            ModuleInstance.from_dict(n, (manifests or {}).get(n.get("manifest_id")))
            for n in raw_nodes
        ]
        all_edges = [Edge.from_dict(e) for e in raw_edges]

        # 后端只负责 python 通道的 source / transform；
        # js 源的帧在浏览器里由 web/streams.js 生成，render 节点只在前端重绘。
        self.nodes = [
            n for n in all_nodes
            if n.mode == "stream"
            and n.stream_role in ("source", "transform")
            and n.runtime == "python"
            and n.source_mode != "js"
        ]
        keep = {n.node_id for n in self.nodes}

        # 只保留「从 python 源可达」的子图：某个源切成浏览器 JS 后，
        # 挂在它下游的 python transform 不该继续空跑并推空帧覆盖前端画面。
        py_sources = {
            n.node_id for n in self.nodes
            if n.stream_role == "source" and n.source_mode != "js"
        }
        reachable = set(py_sources)
        if py_sources:
            changed = True
            while changed:
                changed = False
                for e in all_edges:
                    if e.src in reachable and e.dst in keep and e.dst not in reachable:
                        reachable.add(e.dst)
                        changed = True
        # python 源全部切成 JS 时 reachable 为空 —— 后端不启动任何 tick 线程
        self.nodes = [n for n in self.nodes if n.node_id in reachable]
        keep = {n.node_id for n in self.nodes}

        self.edges = [e for e in all_edges if e.src in keep and e.dst in keep]
        self._by_id = {n.node_id: n for n in self.nodes}
        self.order = self._topo()
        self.node_count = len(self.nodes)

        self._states: dict[str, dict] = {n.node_id: {} for n in self.nodes}
        self._generators: dict[str, Any] = {}
        self._compiled: dict[str, tuple[dict, Any, str]] = {}
        self._stdout: dict[str, str] = {}

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def start(self) -> "StreamJob":
        self._thread = threading.Thread(
            target=self._loop, name=f"dataflow-stream-{self.run_id}", daemon=True
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self.finished = True
        with self._sub_lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                while not q.empty():
                    q.get_nowait()
                q.put_nowait(None)          # sentinel：让 SSE handler 结束循环
            except queue.Full:
                pass

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=QUEUE_MAX)
        with self._sub_lock:
            self._subscribers.append(q)
            self._last_sub_ts = time.time()
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._sub_lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def status(self) -> dict:
        return {
            "run_id": self.run_id,
            "running": not self._stop.is_set(),
            "frame": self.frame_no,
            "fps": round(self.fps, 2),
            "interval_ms": self.interval_ms,
            "node_count": self.node_count,
            "nodes": list(self.order),
            "uptime_s": round(time.time() - self.started_at, 1),
            "subscribers": len(self._subscribers),
            "last_error": self.last_error,
        }

    # ------------------------------------------------------------------ #
    # 帧生成
    # ------------------------------------------------------------------ #
    def _loop(self) -> None:
        while not self._stop.is_set():
            with self._sub_lock:
                has_subscriber = bool(self._subscribers)
            if has_subscriber:
                self._last_sub_ts = time.time()
            elif self.frame_no > 0 and time.time() - self._last_sub_ts > IDLE_TIMEOUT:
                # 页面被关闭 / 刷新且没有新订阅者时，别让后台线程一直空转
                self._stop.set()
                break
            t0 = time.perf_counter()
            try:
                frame = self._make_frame()
            except Exception as exc:               # pragma: no cover - 兜底
                self.last_error = f"{type(exc).__name__}: {exc}"
                traceback.print_exc()
                frame = {
                    "run_id": self.run_id, "frame": self.frame_no + 1,
                    "ts": time.time(), "source": "python", "root": "",
                    "by_node": {}, "payload": None, "tree": {},
                    "meta": {"errors": [self.last_error], "fatal": True},
                }
            self._broadcast(frame)
            elapsed = time.perf_counter() - t0
            self._frame_times.append(elapsed)
            if len(self._frame_times) > 12:
                self._frame_times.pop(0)
            self.fps = round(1.0 / max(self.interval_ms / 1000.0, 0.001), 2)
            if self._stop.wait(max(0.0, self.interval_ms / 1000.0 - elapsed)):
                break
        self.finished = True

    def _broadcast(self, frame: dict) -> None:
        with self._sub_lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                if q.full():
                    q.get_nowait()               # 丢最旧的一帧，宁可跳帧也不积压
                q.put_nowait(frame)
            except (queue.Full, queue.Empty):
                pass

    def _make_frame(self) -> dict:
        self.frame_no += 1
        ts = time.time()
        by_node: dict[str, dict] = {}
        trees: dict[str, dict] = {}
        errors: list[str] = []
        include_tree = (self.frame_no % TREE_EVERY == 1)
        self._stdout.clear()

        for node_id in self.order:
            node = self._by_id[node_id]
            inputs = self._collect_inputs(node_id, by_node)
            try:
                raw, local_vars = self._call_node(node, inputs, ts)
                outputs = normalize_outputs(raw, [p.name for p in node.outputs])
                by_node[node_id] = outputs
                if include_tree:
                    trees[node_id] = {
                        "inputs": build_tree(inputs),
                        "outputs": build_tree(outputs),
                        "locals": build_tree(clean_locals(local_vars)),
                    }
            except StopIteration:
                errors.append(f"模块 {node.name}（{node_id}）的生成器已结束")
                by_node[node_id] = {}
                self._stop.set()
            except Exception as exc:
                msg = f"{node.name}（{node_id}）：{type(exc).__name__}: {exc}"
                errors.append(msg)
                self.last_error = msg
                by_node[node_id] = {}
                if include_tree:
                    trees[node_id] = {"inputs": build_tree(inputs), "outputs": [], "locals": []}

        root = self.order[0] if self.order else ""
        root_payload: Any = None
        if root and by_node.get(root):
            root_payload = next(iter(by_node[root].values()), None)

        meta: dict[str, Any] = {
            "interval_ms": self.interval_ms,
            "fps": self.fps,
            "node_count": self.node_count,
            "nodes": list(self.order),
            "errors": errors,
            "stdout": {k: v[-400:] for k, v in self._stdout.items() if v.strip()},
            "avg_exec_ms": round(sum(self._frame_times) / len(self._frame_times) * 1000, 2)
            if self._frame_times else 0,
        }
        if self._stop.is_set() and not errors:
            meta["ended"] = True

        return {
            "run_id": self.run_id,
            "frame": self.frame_no,
            "ts": ts,
            "source": "python",
            "root": root,
            "by_node": jsonable(by_node),
            "payload": jsonable(root_payload),
            "tree": trees if include_tree else {},
            "meta": meta,
        }

    def _collect_inputs(self, node_id: str, by_node: dict[str, dict]) -> dict[str, Any]:
        """流子图内按端口名传原生对象；支持按字段名拆包上游的单端口 dict。"""
        inputs: dict[str, Any] = {}
        for edge in self.edges:
            if edge.dst != node_id:
                continue
            up_out = by_node.get(edge.src) or {}
            if edge.src_port in up_out:
                value = up_out[edge.src_port]
            elif len(up_out) == 1:
                value = next(iter(up_out.values()))
            else:
                value = None
            if isinstance(value, dict) and edge.dst_port in value:
                value = value[edge.dst_port]
            inputs[edge.dst_port] = value
        return inputs

    def _call_node(self, node: ModuleInstance, inputs: dict, ts: float) -> tuple[Any, dict]:
        # 生成器风格：首次调用得到 generator，之后每帧 next()
        if node.node_id in self._generators:
            gen = self._generators[node.node_id]
            raw = next(gen)
            frame_obj = getattr(gen, "gi_frame", None)
            local_vars = dict(frame_obj.f_locals) if frame_obj is not None else {}
            return raw, local_vars

        namespace, fn, used_fn = self._compiled.get(node.node_id) or self._compile(node)
        extra = {
            "frame": self.frame_no,
            "state": self._states[node.node_id],
            "ts": ts,
            "interval_ms": self.interval_ms,
        }
        args, kwargs = build_call(fn, inputs, dict(node.params), node.entry_fn, extra=extra)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            raw, local_vars = call_and_capture_locals(fn, args, kwargs)
        if buf.getvalue():
            self._stdout[node.node_id] = self._stdout.get(node.node_id, "") + buf.getvalue()

        # def stream(...) 里带 yield：把 generator 缓存下来，从第二帧起 next()
        if hasattr(raw, "__next__") and hasattr(raw, "gi_frame"):
            self._generators[node.node_id] = raw
            first = next(raw)
            frame_obj = getattr(raw, "gi_frame", None)
            local_vars = dict(frame_obj.f_locals) if frame_obj is not None else local_vars
            return first, local_vars
        return raw, local_vars

    def _compile(self, node: ModuleInstance) -> tuple[dict, Any, str]:
        if node.script:
            source = node.script
            path = f"<inline:{node.node_id}>"
        else:
            path = node.entry or ""
            if not path:
                raise ValueError(f"流节点 {node.node_id} 没有可执行脚本")
            full = path if os.path.isabs(path) else os.path.normpath(os.path.join(self.base_dir, path))
            with open(full, "r", encoding="utf-8") as f:
                source = f.read()
        if not source.strip():
            raise ValueError(f"流节点 {node.node_id} 的脚本为空")

        namespace: dict[str, Any] = {
            "__name__": "__dataflow_stream_module__",
            "__file__": path,
            "__builtins__": __builtins__,
            "PARAMS": dict(node.params),
            "Frame": Frame,
        }
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            exec(compile(source, path, "exec"), namespace)
        fn, used_fn = find_entry(namespace, source, node.entry_fn)
        compiled = (namespace, fn, used_fn)
        self._compiled[node.node_id] = compiled
        return compiled

    @staticmethod
    def _topo_of(nodes: list[ModuleInstance], edges: list[Edge]) -> list[str]:
        ids = [n.node_id for n in nodes]
        id_set = set(ids)
        indeg = {i: 0 for i in ids}
        adj: dict[str, list[str]] = {i: [] for i in ids}
        for e in edges:
            if e.src in id_set and e.dst in id_set:
                adj[e.src].append(e.dst)
                indeg[e.dst] += 1
        ready = [i for i in ids if indeg[i] == 0]
        order: list[str] = []
        while ready:
            u = ready.pop(0)
            order.append(u)
            for v in adj[u]:
                indeg[v] -= 1
                if indeg[v] == 0:
                    ready.append(v)
        # 成环时把剩余节点追加在后面，让画面仍能看到部分数据并给出错误提示
        order += [i for i in ids if i not in order]
        return order

    def _topo(self) -> list[str]:
        return self._topo_of(self.nodes, self.edges)


class StreamHub:
    """全局单例：一个画布只允许一条活跃流，避免多个 tick 线程抢脚本状态。"""

    def __init__(self):
        self._jobs: dict[str, StreamJob] = {}
        self._lock = threading.Lock()

    def start(self, base_dir: str, nodes: list[dict], edges: list[dict],
              interval_ms: int = 600, manifests: dict | None = None) -> StreamJob | None:
        self.stop_all()
        job = StreamJob(base_dir, nodes, edges, interval_ms, manifests)
        if job.node_count == 0:
            return None                   # 画布上只有 js 源，无需后端 SSE
        job.start()
        with self._lock:
            self._jobs[job.run_id] = job
        return job

    def get(self, run_id: str) -> StreamJob | None:
        with self._lock:
            return self._jobs.get(run_id)

    def stop(self, run_id: str | None = None) -> int:
        targets = []
        with self._lock:
            if run_id and run_id in self._jobs:
                targets = [self._jobs.pop(run_id)]
            elif not run_id:
                targets = list(self._jobs.values())
                self._jobs.clear()
        for job in targets:
            job.stop()
        return len(targets)

    def stop_all(self) -> None:
        self.stop()

    def active(self) -> StreamJob | None:
        with self._lock:
            for job in reversed(list(self._jobs.values())):
                if not job._stop.is_set():
                    return job
        return None

    def status(self) -> dict:
        job = self.active()
        return {"running": bool(job), **(job.status() if job else {})}


HUB = StreamHub()
