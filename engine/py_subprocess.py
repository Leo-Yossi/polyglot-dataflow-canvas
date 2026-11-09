"""
py_subprocess.py — 在选定的外部 Python 解释器里执行模块

内置解释器走 PythonAdapter 的 in-process exec（保留原生对象引用）；
外部解释器无法共享内存，因此走子进程 + JSON 桥接：
  inputs  --encode_transport--> 子进程 --decode_transport--> 原生 Python 对象
  outputs --encode_transport--> 主进程 --decode_transport--> 还原 bytes / Frame
变量树在两侧都按同一套 build_tree 生成，type_label 依然是 builtins.list 等原生类型。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import traceback

from .core import ExecutionContext, ExecutionError, ExecutionResult, ModuleInstance
from .environments import PyEnv
from .payload import encode_transport, decode_transport
from .var_tree import build_tree

SENTINEL = "__DATAFLOW_RESULT__"
RUNNER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runner.py")


def _resolve(base_dir: str, entry: str) -> str:
    return entry if os.path.isabs(entry) else os.path.normpath(os.path.join(base_dir, entry))


def _tail(text: str, limit: int = 800) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else "…" + text[-limit:]


def _input_view(inputs: dict, module: ModuleInstance) -> dict:
    if inputs or not module.params:
        return inputs
    return {f"params.{k}": v for k, v in module.params.items()}


def run_in_env(module: ModuleInstance, inputs: dict, ctx: ExecutionContext, env: PyEnv) -> ExecutionResult:
    started = time.perf_counter()
    label = f"python · {env.version} · {env.source} (subprocess)"

    path = ""
    inline = module.script or ""
    if not inline:
        path = _resolve(ctx.base_dir, module.entry)
        if not os.path.isfile(path):
            raise ExecutionError(f"找不到脚本文件：{module.entry or '<未指定>'}")

    request = {
        "script_path": path,
        "script": inline,
        "entry_fn": module.entry_fn or "",
        "params": dict(module.params),
        "inputs": encode_transport(inputs),
        "output_names": [p.name for p in module.outputs],
    }
    child_env = os.environ.copy()
    child_env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})

    try:
        proc = subprocess.run(
            [env.exe, RUNNER],
            input=json.dumps(request, ensure_ascii=False).encode("utf-8"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=ctx.base_dir, env=child_env, timeout=60,
        )
    except subprocess.TimeoutExpired as exc:
        raise ExecutionError("外部 Python 环境执行超时（60s）", stderr_tail=str(exc)) from exc
    except Exception as exc:
        raise ExecutionError(f"无法启动外部 Python 环境 {env.exe}：{exc}",
                             stderr_tail=traceback.format_exc()) from exc

    stdout = (proc.stdout or b"").decode("utf-8", errors="replace")
    stderr_text = (proc.stderr or b"").decode("utf-8", errors="replace")
    payload = None
    for line in reversed(stdout.splitlines()):
        if line.startswith(SENTINEL):
            try:
                payload = json.loads(line[len(SENTINEL):])
            except json.JSONDecodeError:
                pass
            break

    if payload is None:
        raise ExecutionError(
            f"外部解释器未返回执行结果（exit={proc.returncode}）",
            stderr_tail=_tail(traceback.format_exc() + "\n" + stderr_text + "\n" + stdout),
            exit_code=proc.returncode or 1,
        )
    if not payload.get("ok"):
        raise ExecutionError(
            f"脚本在 {env.label} 中执行失败：{payload.get('error', '未知错误')}",
            stderr_tail=_tail(str(payload.get("traceback") or "") + "\n" + stderr_text),
            exit_code=1,
        )

    outputs = decode_transport(payload.get("outputs") or {})
    local_vars = decode_transport(payload.get("locals") or {})
    used_fn = payload.get("entry_fn") or module.entry_fn or "execute"
    tree = {
        "inputs": build_tree(_input_view(inputs, module)),
        "outputs": build_tree(outputs),
        "locals": build_tree(local_vars),
    }
    duration = round((time.perf_counter() - started) * 1000, 2)
    return ExecutionResult(
        node_id=module.node_id,
        runtime_label=label,
        outputs=outputs,
        variable_tree=tree,
        meta={
            "exit_code": 0,
            "duration_ms": duration,
            "runtime_label": label,
            "stderr_tail": _tail(str(payload.get("stderr") or "") + "\n" + stderr_text),
            "stdout_tail": _tail(str(payload.get("stdout") or "")),
            "entry": module.entry or "<inline>",
            "entry_fn": used_fn,
            "env_id": env.id,
            "env_label": env.label,
            "env_exe": env.exe,
            "env_version": env.version,
            "bridge": "JSON + base64（跨解释器，无法共享对象引用）",
            "captured_locals": sorted(local_vars.keys()),
        },
    )
