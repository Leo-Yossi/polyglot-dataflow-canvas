"""
environments.py — Python 解释器环境探测与管理

节点可以选择「在哪个 Python 环境里跑」：
  - __inprocess__：服务进程内置解释器，exec 直接执行，保留原生对象引用与局部变量
  - 其余：外部解释器（venv / conda / 系统 Python），通过子进程 + JSON 桥接执行

安全约束：只有本模块探测 / 登记过的解释器路径会被真正执行，
前端不能传入任意命令行。
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass

INPROCESS_ID = "__inprocess__"
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CUSTOM_FILE = os.path.join(BASE_DIR, "envs.json")
CACHE_TTL = 60.0
MAX_CANDIDATES = 40

_PROBE_CODE = (
    "import sys,json;"
    "print(json.dumps({'version':sys.version.split()[0],'prefix':sys.prefix,'exe':sys.executable}))"
)

_lock = threading.Lock()
_cache: list["PyEnv"] = []
_cache_at = 0.0


@dataclass
class PyEnv:
    id: str
    label: str
    exe: str
    version: str
    prefix: str
    kind: str          # in-process | external
    source: str        # server | venv | conda | PATH | system | custom
    path: str = ""     # 展示用的短路径

    def to_dict(self) -> dict:
        return asdict(self)


def _short(path: str) -> str:
    try:
        rel = os.path.relpath(path, BASE_DIR)
        return rel if not rel.startswith("..") else path
    except ValueError:
        return path


def _source_of(path: str) -> str:
    low = path.replace("\\", "/").lower()
    if "conda" in low or "anaconda" in low or "miniconda" in low:
        return "conda"
    if "/envs/" in low or "\\envs\\" in low or "venv" in low:
        return "venv"
    if "appdata" in low or "/usr/bin" in low or "programs/python" in low:
        return "system"
    return "local"


def _label(path: str, version: str, source: str, custom: bool = False) -> tuple[str, str]:
    """返回 (标签, 来源标记)。"""
    low = path.replace("\\", "/")
    parts = [p for p in low.split("/") if p]
    name = ""
    if source == "conda":
        if "envs" in parts:
            name = parts[parts.index("envs") + 1] if parts.index("envs") + 1 < len(parts) else "base"
        else:
            name = "base"
        return f"conda · {name} · Python {version}", "conda"
    if source == "venv":
        for p in reversed(parts):
            if p in ("bin", "scripts", "python", "python.exe"):
                continue
            name = p
            break
        return f"venv · {name} · Python {version}", "venv"
    if custom:
        return f"自定义 · Python {version}", "custom"
    return f"Python {version} · {_short(path)}", source


def _probe(exe: str) -> dict | None:
    try:
        proc = subprocess.run(
            [exe, "-c", _PROBE_CODE],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=4, cwd=BASE_DIR,
        )
        if proc.returncode != 0:
            return None
        line = (proc.stdout or b"").decode("utf-8", errors="replace").strip().splitlines()
        if not line:
            return None
        info = json.loads(line[-1])
        return {"exe": os.path.realpath(info.get("exe") or exe),
                "version": str(info.get("version", "?")),
                "prefix": str(info.get("prefix", ""))}
    except Exception:
        return None


def _custom_paths() -> list[str]:
    if not os.path.isfile(CUSTOM_FILE):
        return []
    try:
        with open(CUSTOM_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [str(p) for p in (data.get("paths") or []) if isinstance(p, str)]
    except Exception:
        return []


def _save_custom(paths: list[str]) -> None:
    with open(CUSTOM_FILE, "w", encoding="utf-8") as f:
        json.dump({"paths": paths}, f, ensure_ascii=False, indent=2)


def _candidates() -> list[str]:
    """收集候选解释器路径：项目 venv、用户目录 conda/venv、PATH、常见安装位置。"""
    out: list[str] = []
    home = os.path.expanduser("~")

    def add(path: str):
        if path and os.path.isfile(path):
            out.append(os.path.abspath(path))

    # 1) 项目内虚拟环境
    for root in [BASE_DIR, os.path.join(BASE_DIR, "..")]:
        for name in ("venv", ".venv", "env", ".env"):
            base = os.path.join(root, name)
            add(os.path.join(base, "Scripts", "python.exe"))
            add(os.path.join(base, "bin", "python"))
        for base in glob.glob(os.path.join(root, "envs", "*")):
            add(os.path.join(base, "Scripts", "python.exe"))
            add(os.path.join(base, "bin", "python"))

    # 2) 用户目录常见 conda / venv 布局
    for base in (
        os.path.join(home, "anaconda3"), os.path.join(home, "miniconda3"),
        os.path.join(home, "AppData", "Local", "anaconda3"),
        os.path.join(home, "AppData", "Local", "Programs", "Python"),
        os.path.join(home, "venvs"), os.path.join(home, "envs"),
        "C:\\ProgramData\\anaconda3", "C:\\ProgramData\\miniconda3",
    ):
        add(os.path.join(base, "python.exe"))
        add(os.path.join(base, "bin", "python"))
        for sub in glob.glob(os.path.join(base, "envs", "*")) + glob.glob(os.path.join(base, "Python3*")) + glob.glob(os.path.join(base, "*")):
            add(os.path.join(sub, "python.exe"))
            add(os.path.join(sub, "bin", "python"))
            add(os.path.join(sub, "Scripts", "python.exe"))

    # 3) PATH 上所有 python 可执行（跳过 py launcher / pythonw）
    for name in ("python3", "python"):
        found = shutil.which(name)
        if found:
            add(found)
    for folder in (os.environ.get("PATH") or "").split(os.pathsep):
        if not folder or not os.path.isdir(folder):
            continue
        patterns = ["python*.exe"] if os.name == "nt" else ["python3*", "python"]
        for pat in patterns:
            for hit in glob.glob(os.path.join(folder, pat)):
                low = os.path.basename(hit).lower()
                if (low.startswith("pythonw") or (low.startswith("python") and low.endswith("w.exe"))
                        or low.endswith((".dll", ".cfg"))):
                    continue
                if os.name != "nt" and not os.access(hit, os.X_OK):
                    continue
                add(hit)

    # 去重（Windows 大小写不敏感）
    seen: set[str] = set()
    uniq: list[str] = []
    for p in out:
        key = os.path.realpath(p).lower()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)
    return uniq


def _env_id(exe: str) -> str:
    return "env_" + hashlib.md5(os.path.realpath(exe).lower().encode("utf-8")).hexdigest()[:10]


def _builtin() -> PyEnv:
    version = sys.version.split()[0]
    return PyEnv(
        id=INPROCESS_ID,
        label=f"内置 · in-process · Python {version}",
        exe=sys.executable,
        version=version,
        prefix=sys.prefix,
        kind="in-process",
        source="server",
        path=_short(sys.executable),
    )


def discover(force: bool = False) -> list[PyEnv]:
    """返回可用环境列表（内置永远排第一）。"""
    global _cache, _cache_at
    with _lock:
        if not force and _cache and (time.time() - _cache_at) < CACHE_TTL:
            return list(_cache)

        builtin = _builtin()
        builtin_real = os.path.realpath(sys.executable).lower()
        custom = _custom_paths()
        candidates = custom + [p for p in _candidates() if os.path.realpath(p).lower() != builtin_real]
        candidates = candidates[:MAX_CANDIDATES]

        probed: list[tuple[str, dict, bool]] = []
        if candidates:
            with ThreadPoolExecutor(max_workers=8) as pool:
                for path, info in zip(candidates, pool.map(_probe, candidates)):
                    if info:
                        probed.append((path, info, path in custom))

        envs = [builtin]
        seen = {builtin_real}
        for original, info, is_custom in probed:
            key = os.path.realpath(info["exe"]).lower()
            if key in seen:
                continue
            seen.add(key)
            source = "custom" if is_custom else _source_of(original)
            label, src = _label(original, info["version"], source, is_custom)
            envs.append(PyEnv(
                id=_env_id(info["exe"]),
                label=label,
                exe=info["exe"],
                version=info["version"],
                prefix=info["prefix"],
                kind="external",
                source=src,
                path=_short(info["exe"]),
            ))
        _cache, _cache_at = envs, time.time()
        return list(envs)


def resolve(env_id: str | None) -> PyEnv:
    """把前端传来的 env_id 解析成已登记环境；未知 id 直接拒绝。"""
    if not env_id or env_id == INPROCESS_ID or env_id == "in-process":
        return _builtin()
    envs = discover()
    for env in envs:
        if env.id == env_id:
            return env
    for env in envs[1:]:
        if os.path.normcase(os.path.abspath(env_id)) == os.path.normcase(env.exe):
            return env
    raise ValueError(f"未登记的 Python 环境：{env_id}（请先在环境下拉里刷新或登记路径）")


def register(path: str) -> PyEnv:
    """登记一个自定义解释器路径，持久化到 envs.json。"""
    path = os.path.abspath(os.path.expanduser(str(path).strip().strip('"').strip("'")))
    base = os.path.basename(path).lower()
    if not os.path.isfile(path):
        raise ValueError(f"路径不存在或不是文件：{path}")
    if not (base.startswith("python") or base.startswith("pypy")) or "pythonw" in base:
        raise ValueError("请选择 Python 解释器可执行文件（python.exe / python3 / pypy3）")
    info = _probe(path)
    if not info:
        raise ValueError("该路径不是可运行的 Python 解释器，或探测版本信息失败")
    # 登记的就是服务自身：直接复用内置环境，不写入自定义列表
    if os.path.normcase(os.path.realpath(info["exe"])) == os.path.normcase(os.path.realpath(sys.executable)):
        return _builtin()

    paths = _custom_paths()
    if path not in paths:
        paths.append(path)
        _save_custom(paths)
    discover(force=True)
    env = next((e for e in discover() if os.path.normcase(e.exe) == os.path.normcase(info["exe"])), None)
    if env is None:  # 理论上不会发生
        raise ValueError("环境登记后未能载入列表")
    env.label, env.source = _label(path, env.version, "local", custom=True)
    return env


def health() -> dict:
    envs = discover()
    return {
        "default": INPROCESS_ID,
        "count": len(envs),
        "external": sum(1 for e in envs if e.kind == "external"),
        "current": sys.version.split()[0],
    }
