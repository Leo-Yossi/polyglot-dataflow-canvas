"""
adapters.py — 运行时适配器

需求文档 3.2：
  class RuntimeAdapter(ABC):
      @abstractmethod
      def execute(self, module, inputs, ctx) -> ExecutionResult

本版实现：
  - PythonAdapter：exec 加载脚本，调用约定的 def execute(inputs)，
    用 sys.settrace 在 return 事件抓取函数局部变量生成变量树。
  - ShellAdapter：subprocess 执行，stdin 传输入字节，stdout 收输出字节，
    变量树只展示原始文本摘要。
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import time
import traceback
from abc import ABC, abstractmethod
from typing import Any

from .core import ExecutionContext, ExecutionError, ExecutionResult, ModuleInstance
from .entry import (
    build_call,
    call_and_capture_locals,
    clean_locals,
    find_entry,
    normalize_outputs,
)
from .environments import INPROCESS_ID, resolve as resolve_env
from .payload import jsonable as _jsonable, to_bytes
from .var_tree import Frame, build_tree, make_node


# --------------------------------------------------------------------------- #
# 接口
# --------------------------------------------------------------------------- #

class RuntimeAdapter(ABC):
    runtime_label: str = "unknown"

    @abstractmethod
    def execute(self, module: ModuleInstance, inputs: dict, ctx: ExecutionContext) -> ExecutionResult:
        """执行一个模块实例，返回按输出端口名组织的载荷 + 变量树快照 + meta。"""
        raise NotImplementedError


class UnsupportedAdapter(RuntimeAdapter):
    """mcp / http 留待后续迭代，这里给出明确提示而不是静默失败。"""

    def __init__(self, runtime: str):
        self.runtime_label = f"{runtime} (未实现)"

    def execute(self, module, inputs, ctx) -> ExecutionResult:
        raise ExecutionError(
            f"runtime '{module.runtime}' 不在本版范围内（本版支持 python / shell，mcp、http 留待后续迭代）"
        )


# --------------------------------------------------------------------------- #
# 通用工具
# --------------------------------------------------------------------------- #

# _jsonable / to_bytes 由 payload.py 提供；此处 import 后保持 ShellAdapter 原语义不变。
def _resolve(base_dir: str, entry: str) -> str:
    if os.path.isabs(entry):
        return entry
    return os.path.normpath(os.path.join(base_dir, entry))


def _tail(text: str, limit: int = 800) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else "…" + text[-limit:]


def _input_view(inputs: dict, module: ModuleInstance) -> dict:
    """没有输入端口的节点，把配置参数也放进输入变量树，方便逐节点对照查看。"""
    if inputs or not module.params:
        return inputs
    return {f"params.{k}": v for k, v in module.params.items()}


PLACEHOLDER_SCRIPT = '''# 适配器占位节点：默认做透传 / JSON 桥接，可双击编辑
def execute(inputs):
    payload = next(iter(inputs.values()), None)
    if isinstance(payload, bytes):
        return {"out": payload}
    if isinstance(payload, str):
        return {"out": payload.encode("utf-8")}
    import json
    return {"out": json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")}
'''


# --------------------------------------------------------------------------- #
# PythonAdapter
# --------------------------------------------------------------------------- #

class PythonAdapter(RuntimeAdapter):
    runtime_label = "python · in-process exec"

    def execute(self, module: ModuleInstance, inputs: dict, ctx: ExecutionContext) -> ExecutionResult:
        # 环境选择：内置解释器保留对象引用；外部 venv / conda 走子进程 JSON 桥接
        try:
            env = resolve_env(module.env_id)
        except ValueError as exc:
            raise ExecutionError(str(exc)) from exc
        if env.kind == "external":
            from .py_subprocess import run_in_env  # 延迟 import，避免与 runner 循环依赖
            return run_in_env(module, inputs, ctx, env)

        started = time.perf_counter()
        stdout_buf, stderr_buf = io.StringIO(), io.StringIO()
        source = ""
        script_path = ""

        try:
            if module.script:
                source = module.script
            elif module.is_placeholder:
                source = PLACEHOLDER_SCRIPT
            else:
                script_path = _resolve(ctx.base_dir, module.entry)
                with open(script_path, "r", encoding="utf-8") as f:
                    source = f.read()

            if not source.strip():
                raise ExecutionError(f"模块脚本为空：{module.entry or module.node_id}")

            # 命名空间：注入参数与零依赖 Frame，脚本可直接用
            namespace: dict[str, Any] = {
                "__name__": "__dataflow_module__",
                "__file__": script_path or f"<inline:{module.node_id}>",
                "__builtins__": __builtins__,
                "PARAMS": dict(module.params),
                "Frame": Frame,
            }
            filename = script_path or f"<inline:{module.node_id}>"

            with contextlib.redirect_stdout(stdout_buf), contextlib.redirect_stderr(stderr_buf):
                exec(compile(source, filename, "exec"), namespace)
                fn, used_fn = find_entry(namespace, source, module.entry_fn)
                # 流式脚本的 tick(frame, state, ...) 在一次性运行时也给它一帧上下文
                stream_ctx = ({"frame": 0, "state": {}, "ts": time.time(),
                               "interval_ms": module.interval_ms}
                              if module.mode == "stream" else None)
                args, kwargs = build_call(fn, inputs, dict(module.params),
                                          module.entry_fn, extra=stream_ctx)
                raw_result, local_vars = call_and_capture_locals(fn, args, kwargs)

            outputs = normalize_outputs(raw_result, [p.name for p in module.outputs])
            captured = clean_locals(local_vars)
            tree = {
                "inputs": build_tree(_input_view(inputs, module)),
                "outputs": build_tree(outputs),
                "locals": build_tree(captured),
            }
            duration = round((time.perf_counter() - started) * 1000, 2)
            return ExecutionResult(
                node_id=module.node_id,
                runtime_label=self.runtime_label,
                outputs=outputs,
                variable_tree=tree,
                meta={
                    "exit_code": 0,
                    "duration_ms": duration,
                    "runtime_label": self.runtime_label,
                    "stderr_tail": _tail(stderr_buf.getvalue()),
                    "stdout_tail": _tail(stdout_buf.getvalue()),
                    "entry": module.entry or "<inline>",
                    "entry_fn": used_fn,
                    "env_id": INPROCESS_ID,
                    "env_label": env.label,
                    "bridge": "in-process 直接传对象引用（零拷贝）",
                    "captured_locals": sorted(captured.keys()),
                },
            )

        except ExecutionError:
            raise
        except Exception as exc:
            raise ExecutionError(
                f"{type(exc).__name__}: {exc}",
                stderr_tail=_tail(traceback.format_exc() + "\n" + stderr_buf.getvalue()),
            ) from exc


# --------------------------------------------------------------------------- #
# ShellAdapter
# --------------------------------------------------------------------------- #

class ShellAdapter(RuntimeAdapter):
    runtime_label = "shell · subprocess"

    def execute(self, module: ModuleInstance, inputs: dict, ctx: ExecutionContext) -> ExecutionResult:
        started = time.perf_counter()
        python_exe = sys.executable
        py_env = None
        if module.env_id:
            try:
                py_env = resolve_env(module.env_id)
                python_exe = py_env.exe
            except ValueError as exc:
                raise ExecutionError(str(exc)) from exc
        command = module.entry.replace("{python}", python_exe)
        # Windows 下未加引号的 python 路径可能含空格
        if "{python}" in module.entry and not module.entry.lstrip().startswith(("'", '"')):
            command = module.entry.replace("{python}", f'"{python_exe}"')

        primary = module.inputs[0].name if module.inputs else next(iter(inputs), None)
        stdin_bytes = to_bytes(inputs.get(primary)) if primary is not None else b""

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        env["DATAFLOW_PARAMS"] = json.dumps(_jsonable(module.params), ensure_ascii=False)

        try:
            proc = subprocess.run(
                command,
                shell=True,
                input=stdin_bytes,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=ctx.base_dir,
                env=env,
                timeout=30,
            )
        except subprocess.TimeoutExpired as exc:
            raise ExecutionError("shell 执行超时（30s）", stderr_tail=str(exc)) from exc
        except Exception as exc:
            raise ExecutionError(f"subprocess 启动失败：{exc}", stderr_tail=traceback.format_exc()) from exc

        stdout = proc.stdout or b""
        stderr_text = (proc.stderr or b"").decode("utf-8", errors="replace")
        outputs = self._normalize_outputs(stdout, module, stdin_bytes)

        # 需求：Shell 变量树只展示原始文本摘要
        tree = {
            "inputs": build_tree(_input_view(inputs, module)),
            "outputs": build_tree(outputs),
            "locals": [],
            "text_summary": [
                make_node("stdin", stdin_bytes),
                make_node("stdout", stdout),
            ],
        }
        duration = round((time.perf_counter() - started) * 1000, 2)
        result = ExecutionResult(
            node_id=module.node_id,
            runtime_label=self.runtime_label,
            outputs=outputs,
            variable_tree=tree,
            meta={
                "exit_code": proc.returncode,
                "duration_ms": duration,
                "runtime_label": self.runtime_label,
                "stderr_tail": _tail(stderr_text),
                "command": command,
                "stdin_bytes": len(stdin_bytes),
                "stdout_bytes": len(stdout),
                "env_id": py_env.id if py_env else INPROCESS_ID,
                "env_label": py_env.label if py_env else "服务内置 Python",
                "env_exe": python_exe,
            },
        )
        if proc.returncode != 0:
            result.error = f"进程退出码 {proc.returncode}：{_tail(stderr_text, 240) or '无 stderr 输出'}"
        return result

    @staticmethod
    def _normalize_outputs(stdout: bytes, module: ModuleInstance, stdin_bytes: bytes) -> dict:
        names = [p.name for p in module.outputs]
        if not names:
            return {"stdout": stdout}
        if len(names) == 1:
            return {names[0]: stdout}
        # 多输出端口：约定脚本打印一个 JSON 对象
        try:
            parsed = json.loads(stdout.decode("utf-8"))
            if isinstance(parsed, dict):
                return {n: to_bytes(parsed.get(n, "")) for n in names}
        except Exception:
            pass
        return {names[0]: stdout, **{n: b"" for n in names[1:]}}


# --------------------------------------------------------------------------- #
# 注册表
# --------------------------------------------------------------------------- #

_REGISTRY: dict[str, RuntimeAdapter] = {
    "python": PythonAdapter(),
    "shell": ShellAdapter(),
}


def get_adapter(runtime: str) -> RuntimeAdapter:
    return _REGISTRY.get(runtime) or UnsupportedAdapter(runtime)


def supported_runtimes() -> list[str]:
    return list(_REGISTRY.keys())
