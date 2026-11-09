"""
payload.py — 载荷序列化

两套语义分开，避免互相干扰：
  - to_bytes()          : 给 stdin / 文件用的「可读字节」，bytes 按 UTF-8 文本展开
  - encode_transport()  : 跨 Python 解释器的 JSON 桥接，bytes 用 base64 保真还原，
                          表格对象带 dtype 还原成 Frame（变量树仍显示列 dtype）
"""

from __future__ import annotations

import base64
import json
from typing import Any

from .var_tree import Frame

BYTES_KEY = "__dataflow_bytes_b64__"
FRAME_KEY = "__dataflow_frame__"
REPR_KEY = "__dataflow_repr__"


def _is_frame(obj: Any) -> bool:
    if isinstance(obj, (dict, list, tuple, set, str, bytes, bytearray, int, float, bool)) or obj is None:
        return False
    return hasattr(obj, "columns") and hasattr(obj, "dtypes")


def _frame_payload(obj: Any) -> dict:
    columns = [str(c) for c in obj.columns]
    try:
        data = {c: [_legacy(v) for v in list(obj[c])] for c in columns}
    except Exception:
        data = {str(k): [_legacy(v) for v in list(vs)] for k, vs in getattr(obj, "data", {}).items()}
    dtypes = {c: str(obj.dtypes[c]) for c in columns} if not isinstance(obj.dtypes, dict) else {
        str(k): str(v) for k, v in obj.dtypes.items()
    }
    return {FRAME_KEY: {"columns": columns, "dtypes": dtypes, "data": data}}


def _legacy(value: Any) -> Any:
    """原 Shell 通道用的宽松 JSON 化：bytes → UTF-8 文本，Frame → 普通 dict。"""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", errors="replace")
    if isinstance(value, Frame):
        return {"columns": value.columns, "dtypes": value.dtypes, "data": value.data}
    if isinstance(value, dict):
        return {str(k): _legacy(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_legacy(v) for v in value]
    if _is_frame(value):
        try:
            return {str(c): _legacy(list(value[c])) for c in value.columns}
        except Exception:
            pass
    return repr(value)


def to_bytes(value: Any) -> bytes:
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    if isinstance(value, str):
        return value.encode("utf-8")
    return json.dumps(_legacy(value), ensure_ascii=False).encode("utf-8")


# 对外别名：Shell 通道与环境变量传参使用
jsonable = _legacy


def encode_transport(value: Any, _depth: int = 0) -> Any:
    """把任意 Python 对象编码成可 JSON 序列化、且能还原原生类型的结构。"""
    if _depth > 12:
        return {REPR_KEY: repr(value)[:200]}
    if isinstance(value, (bytes, bytearray)):
        return {BYTES_KEY: base64.b64encode(bytes(value)).decode("ascii")}
    if _is_frame(value):
        return _frame_payload(value)
    if isinstance(value, dict):
        return {str(k): encode_transport(v, _depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [encode_transport(v, _depth + 1) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return {REPR_KEY: repr(value)[:200]}


def decode_transport(value: Any) -> Any:
    """encode_transport 的逆变换。"""
    if isinstance(value, dict):
        if BYTES_KEY in value and len(value) == 1:
            try:
                return base64.b64decode(value[BYTES_KEY])
            except Exception:
                return b""
        if FRAME_KEY in value and len(value) == 1:
            spec = value[FRAME_KEY] or {}
            try:
                return Frame(spec.get("data") or {}, spec.get("dtypes") or None)
            except Exception:
                return spec.get("data") or {}
        if REPR_KEY in value and len(value) == 1:
            return str(value[REPR_KEY])
        return {k: decode_transport(v) for k, v in value.items()}
    if isinstance(value, list):
        return [decode_transport(v) for v in value]
    return value
