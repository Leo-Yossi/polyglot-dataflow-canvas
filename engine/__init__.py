"""多语言模块化数据流平台（最小版）— 执行引擎包。"""

from .core import (
    Edge,
    EdgeRequiresAdapter,
    ExecutionContext,
    ExecutionError,
    ExecutionResult,
    ModuleInstance,
    ModuleManifest,
    ParamSpec,
    Port,
)
from .var_tree import Frame, build_tree, make_node, type_label, value_preview

__all__ = [
    "Edge", "EdgeRequiresAdapter", "ExecutionContext", "ExecutionError",
    "ExecutionResult", "ModuleInstance", "ModuleManifest", "ParamSpec", "Port",
    "Frame", "build_tree", "make_node", "type_label", "value_preview",
]
