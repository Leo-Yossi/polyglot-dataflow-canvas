"""
runner.py — 外部 Python 解释器里的执行代理（由 py_subprocess.py 启动）

协议：
  stdin  : JSON {script_path, script, entry_fn, params, inputs, output_names}
  stdout : 最后一行 "__DATAFLOW_RESULT__{json}"，脚本自身的 print 被捕获，不污染协议
  stderr : runner 自身崩溃时的 traceback

这里刻意只依赖标准库 + 项目内 engine 模块，让任意 venv/conda 都能直接跑。
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine.entry import (  # noqa: E402
    build_call, call_and_capture_locals, clean_locals,
    find_entry, list_functions, normalize_outputs,
)
from engine.payload import decode_transport, encode_transport  # noqa: E402
from engine.var_tree import Frame  # noqa: E402

SENTINEL = "__DATAFLOW_RESULT__"


def emit(payload: dict) -> None:
    text = SENTINEL + json.dumps(payload, ensure_ascii=False, default=str) + "\n"
    stream = getattr(sys, "__stdout__", sys.stdout)
    buffer = getattr(stream, "buffer", None)
    if buffer is not None:
        buffer.write(text.encode("utf-8"))
        buffer.flush()
    else:  # pragma: no cover
        stream.write(text)
        stream.flush()


def main() -> int:
    request = json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
    source = request.get("script") or ""
    path = request.get("script_path") or ""
    if not source and path and os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            source = f.read()
    if not source.strip():
        emit({"ok": False, "error": f"脚本为空或不存在：{path or '<inline>'}", "traceback": ""})
        return 1

    inputs = decode_transport(request.get("inputs") or {})
    config = request.get("params") or {}
    entry_fn = request.get("entry_fn") or ""
    out_names = request.get("output_names") or []
    namespace = {
        "__name__": "__dataflow_module__",
        "__file__": path or "<inline>",
        "__builtins__": __builtins__,
        "PARAMS": config,
        "Frame": Frame,
    }
    out_buf, err_buf = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
            exec(compile(source, path or "<inline>", "exec"), namespace)
            fn, used = find_entry(namespace, source, entry_fn)
            args, kwargs = build_call(fn, inputs, config, entry_fn)
            result, local_vars = call_and_capture_locals(fn, args, kwargs)

        outputs = normalize_outputs(result, out_names)
        emit({
            "ok": True,
            "outputs": encode_transport(outputs),
            "locals": encode_transport(clean_locals(local_vars)),
            "entry_fn": used,
            "functions": list_functions(source),
            "stdout": out_buf.getvalue()[-4000:],
            "stderr": err_buf.getvalue()[-4000:],
        })
        return 0
    except Exception as exc:
        emit({
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc() + "\n" + err_buf.getvalue(),
            "functions": list_functions(source),
            "stdout": out_buf.getvalue()[-4000:],
        })
        return 1


if __name__ == "__main__":
    sys.exit(main())
