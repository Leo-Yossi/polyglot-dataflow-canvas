"""
entry.py — 脚本入口函数发现与调用约定

用户脚本不再被强制写成 def execute(inputs)：
  1. 用 AST 静态列出脚本里所有顶层函数（不执行脚本，安全）
  2. 节点可任选其中一个作为入口，参数按名字智能注入
  3. def execute(inputs) / execute(inputs, params) 约定继续兼容

参数注入优先级（按形参名）：
  inputs / data?  inputs 特殊；params 特殊；
  形参名命中上游输入端口名 -> 该端口的值
  形参名命中 params schema 的 key -> 参数面板里的值
  有默认值 -> 不传，用脚本默认
  以上都不满足但有上游数据 -> 传第一个上游值（适配 def transform(seq) 这类写法）
"""

from __future__ import annotations

import ast
import inspect
import sys
from typing import Any

_SKIP = object()


# --------------------------------------------------------------------------- #
# 函数发现
# --------------------------------------------------------------------------- #

def list_functions(source: str) -> list[dict]:
    """静态解析顶层函数，返回给前端做入口函数下拉。"""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [{"error": f"脚本语法错误：第 {exc.lineno} 行 {exc.msg}"}]
    out = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        args = []
        pos = node.args.posonlyargs + node.args.args
        defaults = [None] * (len(pos) - len(node.args.defaults)) + list(node.args.defaults)
        for spec, default in zip(pos, defaults):
            try:
                default_text = None if default is None else ast.unparse(default)
            except Exception:
                default_text = "..."
            args.append({"name": spec.arg, "default": default_text})
        if node.args.vararg:
            args.append({"name": "*" + node.args.vararg.arg, "default": None})
        if node.args.kwarg:
            args.append({"name": "**" + node.args.kwarg.arg, "default": None})
        out.append({
            "name": node.name,
            "args": args,
            "signature": f"{node.name}({', '.join(a['name'] + ('=' + a['default'] if a['default'] else '') for a in args)})",
            "doc": (ast.get_docstring(node) or "").strip().splitlines()[0][:100] if ast.get_docstring(node) else "",
            "lineno": node.lineno,
            "async": isinstance(node, ast.AsyncFunctionDef),
            "private": node.name.startswith("_"),
        })
    return out


def find_entry(namespace: dict, source: str, entry_fn: str = "") -> tuple[Any, str]:
    """返回 (可调用对象, 实际使用的函数名)。"""
    funcs = [f for f in list_functions(source) if "name" in f and not f["private"] and not f["async"]]
    names = [f["name"] for f in funcs]

    if entry_fn:
        fn = namespace.get(entry_fn)
        if not callable(fn):
            raise ValueError(f"脚本里找不到入口函数 {entry_fn}()，可用函数：{', '.join(names) or '无'}")
        return fn, entry_fn
    fn = namespace.get("execute")
    if callable(fn):
        return fn, "execute"
    if len(names) == 1:
        return namespace[names[0]], names[0]
    raise ValueError(
        "脚本未定义 def execute(inputs)，且有多个顶层函数；请在节点面板里指定入口函数。"
        f"可用函数：{', '.join(names) or '无'}"
    )


# --------------------------------------------------------------------------- #
# 调用
# --------------------------------------------------------------------------- #

def build_call(fn, inputs: dict, config: dict, entry_fn: str = "",
               extra: dict | None = None) -> tuple[tuple, dict]:
    """按形参名智能注入，返回 (args, kwargs)。

    extra 用于流式引擎注入 frame / state / ts 等每帧上下文，优先级最高。
    """
    extra = extra or {}
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return (), {}

    args: list[Any] = []
    kwargs: dict[str, Any] = {}
    has_varargs = has_varkw = False

    def value_for(name: str, spec: inspect.Parameter) -> Any:
        if name in extra:
            return extra[name]
        if name == "inputs":
            return inputs
        if name in ("params", "PARAMS", "config"):
            return config
        if name in inputs:
            return inputs[name]
        if name in config:
            return config[name]
        if spec.default is not inspect.Parameter.empty:
            return _SKIP
        if inputs:
            return next(iter(inputs.values()))
        return None

    for name, spec in sig.parameters.items():
        if spec.kind == inspect.Parameter.VAR_POSITIONAL:
            has_varargs = True
            continue
        if spec.kind == inspect.Parameter.VAR_KEYWORD:
            has_varkw = True
            continue
        value = value_for(name, spec)
        if value is _SKIP:
            continue
        if spec.kind == inspect.Parameter.POSITIONAL_ONLY:
            args.append(value)
        else:
            kwargs[name] = value

    if has_varargs and not args and not kwargs:
        args = list(inputs.values())
    if has_varkw:
        for k, v in {**config, **inputs}.items():
            if k not in kwargs and k.isidentifier():
                kwargs[k] = v
    return tuple(args), kwargs


def call_and_capture_locals(fn, args: tuple, kwargs: dict) -> tuple[Any, dict]:
    """调试器式：在入口函数 return 事件抓一份完整局部变量快照。"""
    captured: dict[str, Any] = {}
    target_code = getattr(fn, "__code__", None)

    def tracer(frame, event, arg):
        if target_code is not None and frame.f_code is target_code and event == "return":
            captured.update(dict(frame.f_locals))
        return tracer

    previous = sys.gettrace()
    sys.settrace(tracer)
    try:
        result = fn(*args, **kwargs)
    finally:
        sys.settrace(previous)
    return result, captured


def clean_locals(local_vars: dict) -> dict:
    skip = {"inputs", "params", "PARAMS", "__builtins__", "__name__", "__file__"}
    return {
        k: v for k, v in local_vars.items()
        if k not in skip and not k.startswith("__")
        and not inspect.ismodule(v) and not inspect.isfunction(v)
    }


def normalize_outputs(raw: Any, names: list[str] | None = None) -> dict[str, Any]:
    """按输出端口名组织返回值。

    用户任选的入口函数可能返回 list / dict / 标量：
      - dict 且含端口名 -> 视为已按端口组织（约定写法）
      - dict 但完全不含端口名、且只有一个输出端口 -> 整个 dict 作为该端口的值
      - 其它 -> 归到第一个输出端口
    """
    names = names or []
    if isinstance(raw, dict):
        if names and len(names) == 1 and not any(n in raw for n in names):
            return {names[0]: raw}
        return dict(raw)
    return {(names[0] if names else "out"): raw}
