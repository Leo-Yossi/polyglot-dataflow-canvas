"""
var_tree.py — 调试器式变量树

需求文档 3.4：
  - 节点字段 name / type_label / value_preview / children
  - type_label = type(obj).__module__ + "." + type(obj).__name__
  - dict / list 递归生成 children；DataFrame 每列一个 child，type_label 为列 dtype
本环境无 pandas，额外提供零依赖的 Frame 类演示「列 dtype」分支；
若用户机器装了 pandas，真实 DataFrame 会被同一份代码自动识别。
"""

from __future__ import annotations

from typing import Any

MAX_DEPTH = 6
MAX_CHILDREN = 40
PREVIEW_LIMIT = 160


# --------------------------------------------------------------------------- #
# 零依赖表格对象（用于演示 DataFrame 分支）
# --------------------------------------------------------------------------- #

def _infer_dtype(values: list) -> str:
    if not values:
        return "object"
    types = {type(v) for v in values if v is not None}
    if len(types) == 1:
        t = next(iter(types))
        return {int: "int64", float: "float64", str: "object", bool: "bool"}.get(t, "object")
    if types and types <= {int, float}:
        return "float64"
    return "object"


class Frame:
    """轻量表格容器：有 columns / dtypes，即可被变量树按 DataFrame 规则展开。

    这不是 pandas 替代品，只为了让 demo 在零依赖环境下也能展示列 dtype 特性。
    """

    def __init__(self, data: dict[str, list], dtypes: dict[str, str] | None = None):
        self.data = {str(k): list(v) for k, v in data.items()}
        self.dtypes = dtypes or {k: _infer_dtype(v) for k, v in self.data.items()}

    @property
    def columns(self) -> list[str]:
        return list(self.data.keys())

    def __len__(self) -> int:
        return max((len(v) for v in self.data.values()), default=0)

    def __repr__(self) -> str:
        return f"Frame(columns={self.columns!r}, rows={len(self)})"


def _is_frame(obj: Any) -> bool:
    if isinstance(obj, (dict, list, tuple, set, str, bytes, bytearray, int, float, bool)) or obj is None:
        return False
    return hasattr(obj, "columns") and hasattr(obj, "dtypes")


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #

def type_label(obj: Any) -> str:
    t = type(obj)
    return f"{t.__module__}.{t.__name__}"


def value_preview(obj: Any, limit: int = PREVIEW_LIMIT) -> str:
    if isinstance(obj, (bytes, bytearray)):
        raw = bytes(obj)
        head = raw[:48]
        shown = "".join(chr(b) if 32 <= b < 127 else "." for b in head)
        return f"<bytes {len(raw)} B> {shown!r}"
    try:
        text = repr(obj)
    except Exception:  # pragma: no cover - 极端自定义对象
        text = f"<unreprable {type_label(obj)}>"
    text = text.replace("\n", "\\n")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _empty_children(n: int, shown: int, depth: int) -> list[dict]:
    """容器被截断时补一个提示节点。"""
    if n <= shown:
        return []
    return [{
        "name": f"… 还有 {n - shown} 项",
        "type_label": "builtins.str",
        "value_preview": f"共 {n} 项，仅展开前 {shown} 项（深度上限 {depth}）",
        "children": [],
        "kind": "hint",
        "truncated": True,
    }]


def make_node(
    name: str,
    obj: Any,
    depth: int = 0,
    seen: frozenset[int] = frozenset(),
    label_override: str | None = None,
) -> dict:
    node: dict[str, Any] = {
        "name": str(name),
        "type_label": label_override or type_label(obj),
        "value_preview": value_preview(obj),
        "children": [],
        "kind": "scalar",
        "truncated": False,
    }

    if obj is None or isinstance(obj, (str, int, float, bool)):
        return node

    oid = id(obj)
    if oid in seen:
        node["value_preview"] = "<循环引用，已停止展开>"
        node["kind"] = "hint"
        return node
    if depth >= MAX_DEPTH:
        node["value_preview"] = f"{node['value_preview']}  <到达深度上限，未展开>"
        node["truncated"] = True
        return node
    next_seen = seen | {oid}

    # --- bytes：原始文本摘要 + 前若干行 ---
    if isinstance(obj, (bytes, bytearray)):
        raw = bytes(obj)
        node["kind"] = "bytes"
        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines()
        shown = min(len(lines), 12)
        node["children"] = [
            make_node(f"[{i}]", line, depth + 1, next_seen) for i, line in enumerate(lines[:shown])
        ]
        node["children"] += _empty_children(len(lines), shown, MAX_DEPTH)
        node["child_count"] = len(lines)
        return node

    # --- DataFrame 风格对象：每列一个 child，type_label 为列 dtype ---
    if _is_frame(obj):
        node["kind"] = "frame"
        cols = list(obj.columns)
        shown = min(len(cols), MAX_CHILDREN)
        children = []
        for col in cols[:shown]:
            try:
                dtype = obj.dtypes[col] if not isinstance(obj.dtypes, dict) else obj.dtypes.get(col, "object")
            except Exception:
                dtype = "object"
            try:
                series = obj[col] if not isinstance(obj, dict) else obj.data.get(col, [])
                values = list(series)
            except Exception:
                values = []
            child = make_node(str(col), values, depth + 1, next_seen, label_override=str(dtype))
            child["kind"] = "column"
            children.append(child)
        children += _empty_children(len(cols), shown, MAX_DEPTH)
        node["children"] = children
        node["child_count"] = len(cols)
        return node

    # --- dict ---
    if isinstance(obj, dict):
        node["kind"] = "container"
        items = list(obj.items())
        shown = min(len(items), MAX_CHILDREN)
        node["children"] = [make_node(k, v, depth + 1, next_seen) for k, v in items[:shown]]
        node["children"] += _empty_children(len(items), shown, MAX_DEPTH)
        node["child_count"] = len(items)
        return node

    # --- list / tuple / set ---
    if isinstance(obj, (list, tuple, set, frozenset)):
        node["kind"] = "container"
        items = list(obj)
        shown = min(len(items), MAX_CHILDREN)
        node["children"] = [make_node(f"[{i}]", v, depth + 1, next_seen) for i, v in enumerate(items[:shown])]
        node["children"] += _empty_children(len(items), shown, MAX_DEPTH)
        node["child_count"] = len(items)
        return node

    # --- 其它对象：展开 __dict__（若可读）---
    try:
        attrs = {k: v for k, v in vars(obj).items() if not k.startswith("__")}
    except Exception:
        attrs = {}
    if attrs:
        node["kind"] = "object"
        node["children"] = [make_node(k, v, depth + 1, next_seen) for k, v in list(attrs.items())[:MAX_CHILDREN]]
        node["child_count"] = len(attrs)
    return node


def build_tree(mapping: dict[str, Any]) -> list[dict]:
    """把一个 {name: value} 映射转成变量树根节点列表。"""
    return [make_node(k, v) for k, v in mapping.items()]


def bytes_summary_tree(name: str, raw: bytes) -> dict:
    return make_node(name, raw)


if __name__ == "__main__":  # 自测
    import json
    sample = {
        "records": [{"id": 1, "name": "alpha"}, {"id": 2, "name": "beta"}],
        "mixed": [1, "two", 3, "four", 5],
        "frame": Frame({"id": [1, 2], "score": [92.5, 87.0], "name": ["a", "b"]}),
        "raw": b"line1\nline2\n",
    }
    print(json.dumps(build_tree(sample), ensure_ascii=False, indent=2)[:2600])
