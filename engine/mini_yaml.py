"""
mini_yaml.py — 零依赖 YAML 子集解析器

需求文档允许 Manifest 为 YAML/JSON；标准库没有 YAML，这里实现一个够用的子集：
  - 缩进块（mapping / sequence）
  - `- key: value` 形式且后续同缩进字段归属同一个 item
  - 标量：字符串、带引号字符串、int/float/bool/null、内联列表 [a, b]
  - `#` 行注释与行尾注释（引号内的 # 不当注释）

刻意保持小：只覆盖本平台 Manifest 会用到的语法，避免引入 pip 依赖。
"""

from __future__ import annotations

import re
from typing import Any

_NUM_INT = re.compile(r"^[+-]?\d+$")
_NUM_FLOAT = re.compile(r"^[+-]?\d+\.\d+$")


def _strip_comment(text: str) -> str:
    """去掉不在引号内的 # 之后的内容。"""
    quote = None
    for i, ch in enumerate(text):
        if quote:
            if ch == quote and (i == 0 or text[i - 1] != "\\"):
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "#" and (i == 0 or text[i - 1].isspace()):
            return text[:i].rstrip()
    return text.rstrip()


def _split_inline_list(text: str) -> list[str]:
    parts, buf, quote, depth = [], [], None, 0
    for ch in text:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
            buf.append(ch)
        elif ch == "[":
            depth += 1
            buf.append(ch)
        elif ch == "]":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def scalar(text: str) -> Any:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        inner = text[1:-1]
        if text[0] == '"':
            inner = inner.replace(r"\"", '"').replace(r"\n", "\n").replace(r"\t", "\t")
        else:
            inner = inner.replace("''", "'")
        return inner
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        return [scalar(p) for p in _split_inline_list(inner)] if inner else []
    if text.startswith("{") and text.endswith("}"):
        inner = text[1:-1].strip()
        out = {}
        for part in _split_inline_list(inner) if inner else []:
            k, v = split_key_value(part)
            if k:
                out[k] = scalar(v)
        return out
    low = text.lower()
    if low in ("null", "~", ""):
        return None
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if _NUM_INT.match(text):
        return int(text)
    if _NUM_FLOAT.match(text):
        return float(text)
    return text


def split_key_value(text: str) -> tuple[str, str]:
    """按第一个不在引号内的冒号切分。"""
    quote = None
    for i, ch in enumerate(text):
        if quote:
            if ch == quote and (i == 0 or text[i - 1] != "\\"):
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif ch == ":" and (i + 1 == len(text) or text[i + 1].isspace()):
            return text[:i].strip(), text[i + 1:].strip()
    return text.strip(), ""


class _Line:
    __slots__ = ("indent", "marker", "text")

    def __init__(self, indent: int, marker: bool, text: str):
        self.indent = indent
        self.marker = marker  # 是否为 sequence item
        self.text = text

    def __repr__(self):  # pragma: no cover
        return f"_Line({self.indent}, {'seq' if self.marker else 'map'}, {self.text!r})"


def _lex(source: str) -> list[_Line]:
    lines: list[_Line] = []
    for raw in source.splitlines():
        if raw.strip().startswith("\t"):
            raise ValueError("Manifest 不支持 Tab 缩进，请使用空格")
        cleaned = _strip_comment(raw)
        if not cleaned.strip():
            continue
        indent = len(cleaned) - len(cleaned.lstrip(" "))
        body = cleaned[indent:]
        marker = body == "-" or body.startswith("- ")
        if marker:
            body = body[1:].lstrip(" ")
        lines.append(_Line(indent, marker, body))
    return lines


def _collect_children(lines: list[_Line], i: int, parent_indent: int) -> tuple[list[_Line], int]:
    j = i
    while j < len(lines) and lines[j].indent > parent_indent:
        j += 1
    return lines[i:j], j


def _parse_node(lines: list[_Line], i: int, indent: int) -> tuple[Any, int]:
    if i >= len(lines) or lines[i].indent < indent:
        return None, i

    is_seq = lines[i].marker and lines[i].indent == indent
    if is_seq:
        result: list[Any] = []
        while i < len(lines) and lines[i].indent == indent and lines[i].marker:
            head = lines[i].text
            i += 1
            kids, i = _collect_children(lines, i, indent)
            if head == "":
                if kids:
                    value, _ = _parse_node(kids, 0, kids[0].indent)
                else:
                    value = None
            elif split_key_value(head)[0] != head:
                # "- name: data" + 后续更深缩进字段 => 一个 mapping
                child_indent = kids[0].indent if kids else indent + 2
                virtual = [_Line(child_indent, False, head)] + kids
                value, _ = _parse_node(virtual, 0, child_indent)
            else:
                value = scalar(head)
            result.append(value)
        return result, i

    mapping: dict[str, Any] = {}
    while i < len(lines) and lines[i].indent == indent and not lines[i].marker:
        key, raw_value = split_key_value(lines[i].text)
        i += 1
        if not key:
            continue
        if raw_value == "":
            kids, i = _collect_children(lines, i, indent)
            if kids:
                mapping[key], _ = _parse_node(kids, 0, kids[0].indent)
            else:
                mapping[key] = None
        else:
            mapping[key] = scalar(raw_value)
    return mapping, i


def loads(source: str) -> dict:
    lines = _lex(source)
    if not lines:
        return {}
    value, _ = _parse_node(lines, 0, lines[0].indent)
    if not isinstance(value, dict):
        raise ValueError("Manifest 根节点必须是 mapping")
    return value


def load(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return loads(f.read())


if __name__ == "__main__":  # 自测
    sample = '''
id: py_source
name: "Python 数据源"
inputs: []
outputs:
  - name: data
    transport: memory_object
    format_hint: "json:records"
    accepts: []
params:
  - key: count
    type: int
    default: 5
    label: 记录条数 # 备注
'''
    import json
    print(json.dumps(loads(sample), ensure_ascii=False, indent=2))
