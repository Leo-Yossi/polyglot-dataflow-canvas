"""
server.py — 零依赖 HTTP 服务（Python 标准库）

启动：
    python server.py
    python server.py --port 8765

提供：
    GET  /                 前端单页
    GET  /app.js /style.css
    GET  /api/health       后端在线检测
    GET  /api/envs         可用 Python 解释器列表（?refresh=1 重新探测）
    POST /api/envs         登记自定义解释器路径
    GET  /api/modules      全部 Manifest（前端据此渲染节点/端口/参数）
    GET  /api/scripts      modules/ + scripts/ 下所有脚本及其顶层函数
    GET  /api/script       读取脚本内容 + 函数列表
    POST /api/script       保存到指定脚本路径 / 新建脚本
    POST /api/run          拓扑调度执行（一次性批处理）
    POST /api/stream/start 启动后台 tick 线程，真实执行 Python 流数据源
    GET  /api/stream       SSE 订阅帧（event: frame / event: end）
    GET  /api/stream/status 当前流状态
    POST /api/stream/stop  停止推流

仅绑定 127.0.0.1，供本机演示使用。
"""

from __future__ import annotations

import argparse
import glob
import json
import mimetypes
import os
import queue
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from engine import environments as envs_mod  # noqa: E402
from engine.adapters import PLACEHOLDER_SCRIPT, supported_runtimes  # noqa: E402
from engine.core import ModuleManifest  # noqa: E402
from engine.entry import list_functions  # noqa: E402
from engine.mini_yaml import load as yaml_load  # noqa: E402
from engine.scheduler import Scheduler  # noqa: E402
from engine.stream import HUB  # noqa: E402

WEB_DIR = os.path.join(BASE_DIR, "web")
MODULES_DIR = os.path.join(BASE_DIR, "modules")
SCRIPTS_DIR = os.path.join(BASE_DIR, "scripts")


def load_manifests() -> dict[str, ModuleManifest]:
    out: dict[str, ModuleManifest] = {}
    for path in sorted(glob.glob(os.path.join(MODULES_DIR, "*.yaml")) +
                       glob.glob(os.path.join(MODULES_DIR, "*.json"))):
        try:
            data = yaml_load(path) if path.endswith(".yaml") else json.load(open(path, "r", encoding="utf-8"))
            m = ModuleManifest.from_dict(data)
            out[m.id] = m
        except Exception as exc:
            print(f"[warn] 跳过无效 Manifest {os.path.basename(path)}: {exc}", file=sys.stderr)
    return out


def safe_path(relative: str) -> str | None:
    """限制在 BASE_DIR 内，避免路径穿越。"""
    root = os.path.realpath(BASE_DIR)
    path = os.path.realpath(os.path.join(root, relative))
    if path == root or path.startswith(root + os.sep):
        return path
    return None


def script_target(relative: str) -> str | None:
    """脚本只能放在 modules/ 或 scripts/ 下，且必须是 .py，防止任意文件写入。"""
    rel = str(relative or "").replace("\\", "/").lstrip("/")
    parts = rel.split("/")
    if len(parts) != 2 or parts[0] not in ("modules", "scripts") or not parts[1].endswith(".py"):
        return None
    if any(p in ("", ".", "..") for p in parts):
        return None
    return safe_path(rel)


def scan_scripts() -> list[dict]:
    """扫描脚本并用 AST 列出顶层函数，供前端做「脚本 / 入口函数」下拉。"""
    out: list[dict] = []
    for folder in ("modules", "scripts"):
        for path in sorted(glob.glob(os.path.join(BASE_DIR, folder, "*.py"))):
            rel = f"{folder}/{os.path.basename(path)}"
            try:
                with open(path, "r", encoding="utf-8") as f:
                    source = f.read()
                funcs = list_functions(source)
            except Exception as exc:
                funcs = [{"error": str(exc)}]
            usable = [f["name"] for f in funcs if "name" in f and not f["private"] and not f["async"]]
            default_fn = "execute" if "execute" in usable else (usable[0] if len(usable) == 1 else "")
            out.append({
                "path": rel, "name": os.path.basename(path), "folder": folder,
                "size": os.path.getsize(path), "functions": funcs,
                "default_fn": default_fn, "script": source,
            })
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = "DataFlowDemo/0.1"

    # ------------------------------------------------------------------ #
    # 基础响应
    # ------------------------------------------------------------------ #
    def _send(self, status: int, body: bytes, ctype: str):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, status: int = 200):
        self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _file(self, path: str):
        if not os.path.isfile(path):
            self._json({"error": "not found"}, 404)
            return
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if path.endswith((".js", ".css", ".html", ".json")):
            ctype += "; charset=utf-8"
        with open(path, "rb") as f:
            self._send(200, f.read(), ctype)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def _sse(self, run_id: str) -> None:
        """Server-Sent Events：一个订阅者一个队列，断线即退订。"""
        job = HUB.get(run_id)
        if not job:
            self._json({"error": "流不存在或已结束", "run_id": run_id}, 404)
            return
        subscriber = job.subscribe()
        self.protocol_version = "HTTP/1.1"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.close_connection = False
        try:
            self.wfile.write(b": connected\n\n")
            while True:
                try:
                    item = subscriber.get(timeout=12)
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")   # 防止中间设备掐断长连接
                    continue
                if item is None:
                    break
                data = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
                self.wfile.write(("event: frame\ndata: " + data + "\n\n").encode("utf-8"))
            self.wfile.write(b"event: end\ndata: {}\n\n")
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass                                   # 浏览器关闭 EventSource 属正常
        finally:
            job.unsubscribe(subscriber)
            self.close_connection = True

    def log_message(self, fmt, *args):  # 精简日志
        if "/api/" in (self.path or ""):
            super().log_message(fmt, *args)

    # ------------------------------------------------------------------ #
    # GET
    # ------------------------------------------------------------------ #
    def do_GET(self):
        parsed = urlparse(self.path)
        route, query = parsed.path, parse_qs(parsed.query)
        try:
            if route == "/api/health":
                self._json({
                    "ok": True,
                    "engine": "Python 标准库 · 真实执行",
                    "runtimes": supported_runtimes(),
                    "module_count": len(load_manifests()),
                    "python": sys.version.split()[0],
                    "base_dir": BASE_DIR,
                    "envs": envs_mod.health(),
                })
                return
            if route == "/api/envs":
                force = (query.get("refresh") or ["0"])[0] in ("1", "true", "yes")
                items = [e.to_dict() for e in envs_mod.discover(force=force)]
                self._json({"envs": items, "default": envs_mod.INPROCESS_ID,
                            "custom_file": "envs.json"})
                return
            if route == "/api/modules":
                manifests = load_manifests()
                items = [m.to_dict() for m in manifests.values()]
                categories = sorted({m.category for m in manifests.values()})
                self._json({"modules": items, "categories": categories})
                return
            if route == "/api/scripts":
                self._json({"scripts": scan_scripts(),
                            "folders": ["modules", "scripts"]})
                return
            if route == "/api/script":
                rel = (query.get("path") or [""])[0]
                if rel == "__placeholder__":
                    self._json({"path": "__placeholder__", "script": PLACEHOLDER_SCRIPT,
                                "functions": list_functions(PLACEHOLDER_SCRIPT)})
                    return
                path = safe_path(rel)
                if not path or not os.path.isfile(path):
                    self._json({"error": "脚本不存在", "path": rel}, 404)
                    return
                with open(path, "r", encoding="utf-8") as f:
                    source = f.read()
                self._json({"path": rel, "script": source, "functions": list_functions(source)})
                return
            if route == "/api/stream":
                self._sse((query.get("run_id") or [""])[0])
                return
            if route == "/api/stream/status":
                self._json(HUB.status())
                return
            if route in ("/", "/index.html"):
                self._file(os.path.join(WEB_DIR, "index.html"))
                return
            if route in ("/app.js", "/style.css", "/streams.js", "/renderers.js", "/favicon.svg"):
                self._file(os.path.join(WEB_DIR, route.lstrip("/")))
                return
            self._json({"error": f"未知路由 {route}"}, 404)
        except Exception as exc:
            traceback.print_exc()
            self._json({"error": str(exc)}, 500)

    # ------------------------------------------------------------------ #
    # POST
    # ------------------------------------------------------------------ #
    def do_POST(self):
        route = urlparse(self.path).path
        try:
            if route == "/api/run":
                body = self._body()
                nodes, edges = body.get("nodes") or [], body.get("edges") or []
                if not nodes:
                    self._json({"ok": False, "error": "画布上没有节点", "results": {}}, 400)
                    return
                result = Scheduler(BASE_DIR, load_manifests()).run(nodes, edges)
                self._json(result)
                return
            if route == "/api/envs":
                body = self._body()
                try:
                    env = envs_mod.register(body.get("path") or "")
                except ValueError as exc:
                    self._json({"error": str(exc)}, 400)
                    return
                self._json({"ok": True, "env": env.to_dict(),
                            "envs": [e.to_dict() for e in envs_mod.discover()]})
                return
            if route == "/api/script":
                body = self._body()
                script = body.get("script")
                if not isinstance(script, str):
                    self._json({"error": "缺少 script 内容"}, 400)
                    return
                raw = str(body.get("path") or "")
                if not raw and body.get("name"):
                    name = "".join(ch for ch in str(body["name"]) if ch.isalnum() or ch in "_-")
                    raw = f"scripts/{name or 'untitled'}.py"
                if not raw and body.get("node_id"):
                    node_id = "".join(ch for ch in str(body["node_id"]) if ch.isalnum() or ch in "_-")
                    raw = f"scripts/{node_id or 'adapter'}.py"
                path = script_target(raw)
                if not path:
                    self._json({"error": "脚本路径必须位于 modules/ 或 scripts/ 下的 .py 文件"}, 400)
                    return
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(script)
                self._json({"ok": True, "path": raw, "bytes": len(script.encode("utf-8")),
                            "functions": list_functions(script)})
                return
            if route == "/api/stream/start":
                body = self._body()
                try:
                    interval = max(80, min(10000, int(body.get("interval_ms") or 600)))
                except (TypeError, ValueError):
                    interval = 600
                job = HUB.start(BASE_DIR, body.get("nodes") or [], body.get("edges") or [],
                                interval, load_manifests())
                active = HUB.status()
                self._json({
                    "ok": True,
                    "run_id": job.run_id if job else None,
                    "has_backend": bool(job),
                    "python_nodes": job.node_count if job else 0,
                    "interval_ms": interval,
                    "status": active,
                })
                return
            if route == "/api/stream/stop":
                body = self._body()
                stopped = HUB.stop(body.get("run_id") or None)
                self._json({"ok": True, "stopped": stopped, "status": HUB.status()})
                return
            self._json({"error": f"未知路由 {route}"}, 404)
        except Exception as exc:
            traceback.print_exc()
            self._json({"error": str(exc)}, 500)

    def do_OPTIONS(self):
        self._send(200, b"", "text/plain; charset=utf-8")


def main():
    parser = argparse.ArgumentParser(description="多语言模块化数据流平台 demo")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    os.makedirs(SCRIPTS_DIR, exist_ok=True)
    manifests = load_manifests()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print("=" * 68)
    print("  多语言模块化数据流平台（最小版）· 真实执行后端已启动")
    print(f"  地址      http://{args.host}:{args.port}")
    print(f"  Manifest  {len(manifests)} 个模块：{', '.join(manifests)}")
    print(f"  运行时    {', '.join(supported_runtimes())}（mcp / http 留待后续迭代）")
    print(f"  Python    {sys.version.split()[0]}")
    print("  Ctrl+C 停止")
    print("=" * 68)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
