# DataFlow Studio · 多语言模块化数据流平台（最小版）

画布式平台：把不同语言 / 运行时的程序封装成模块，连线拼成数据流；每个模块执行后用**调试器式变量树**查看输入输出数据的原生类型和值；格式不匹配时通过**适配器模块**桥接。

本仓库是一个**全栈真实执行**的 demo：Python 标准库后端真的 `exec` 脚本、真的起 `subprocess`，前端画布连线后点「运行」，结果来自真实执行而非模拟数据。

每个 Python 节点还能**独立选择 Python 解释器环境**（内置 / venv / conda / 系统 Python）、**任意切换脚本文件**、以及**任选脚本里的顶层函数作为入口**，不再被固定 `def execute(inputs)` 绑死。

此外还有**实时流双通道**：后端起 tick 线程真实执行 Python 流脚本、用 SSE 逐帧推送；数据源也能一键切成浏览器 JS，在前端本地按定时器产帧。画布节点里直接内嵌**零依赖手绘 K 线图**（A 股配色，红涨绿跌）与**电路板信号灯**（三色交通灯 + 8 路 LED），帧到即重绘。

---

## 1. 快速启动

GitHub 仓库：<https://github.com/Leo-Yossi/polyglot-dataflow-canvas>

```bash
git clone https://github.com/Leo-Yossi/polyglot-dataflow-canvas.git
cd polyglot-dataflow-canvas
python server.py                # 默认 http://127.0.0.1:8765
# 或指定端口
python server.py --port 9000
```

浏览器打开 `http://127.0.0.1:8765`。**零 pip 依赖**，只需 Python 3.9+（本机验证 3.13.14）。

页面加载后会自动放好 **「K 线 + 电路板信号灯」双实时链路并开始推流**（默认 500 ms/帧），打开就能看到蜡烛图和 LED 在动。

- 顶栏 **「实时演示」** 重新加载双链路；画布工具条的 **「K线示例」/「信号灯示例」** 只放其中一条。
- **「▶ 开始推流 / ■ 停止推流」** 控制启停，旁边可把间隔切成 250 / 500 / 1000 / 2000 ms。
- 点 **「验收示例」** 回到最初的三节点闭环，再点「运行数据流」做一次性批处理；点 **「序列示例」** 体验逐节点选环境 / 脚本 / 入口函数。
- 选中任一实时数据源，在右侧「实时流 · 双通道配置」里把**数据来源**从「后端 Python」切成「浏览器 JS」，即可对比两条通道（画面顶部标的 `DEMO·600519` 会变成 `JS·DEMO600519`）。

一键跑命令行验收（服务需已启动）：

```bash
python verify.py        # 43 项断言，覆盖原 5 条验收 + 环境/脚本/入口函数 + 实时流
```

---

## 2. 目录结构

```
dataflow-demo/
├── server.py                 标准库 HTTP 服务 + API（零依赖）
├── verify.py                 43 项自动化验收（原 5 条 + 环境/脚本/入口函数 + 实时流）
├── envs.json                 用户登记的自定义解释器路径（首次登记时生成）
├── engine/
│   ├── core.py               Port / Manifest / ModuleInstance / Edge / ExecutionResult
│   ├── mini_yaml.py          零依赖 YAML 子集解析器（Manifest 用）
│   ├── var_tree.py           调试器式变量树 + 零依赖 Frame（演示列 dtype）
│   ├── environments.py       自动探测 venv / conda / 系统 Python + 自定义路径登记
│   ├── entry.py              AST 列出顶层函数 + 按形参名注入参数 + 抓局部变量
│   ├── payload.py            to_bytes + 跨解释器 JSON/base64 桥接（bytes / Frame 保真）
│   ├── runner.py             外部解释器里执行的代理（stdin/stdout JSON 协议）
│   ├── py_subprocess.py      启动外部解释器、还原载荷与变量树
│   ├── adapters.py           RuntimeAdapter ABC / PythonAdapter / ShellAdapter
│   ├── scheduler.py          拓扑排序 + transport 数据拼接规则 + 格式兼容提示（跳过 render 节点）
│   └── stream.py            实时流：后台 tick 线程 / SSE 订阅队列 / 帧与变量树快照 / 闲置回收
├── modules/                  15 个 Manifest（.yaml）+ 对应脚本，改完刷新即生效
│   ├── py_source.*          数据源（混合原生类型，验收点）
│   ├── py_adapter.*         一等公民适配器：memory_object → bytes
│   ├── shell_consumer.*     Shell 消费者：stdin bytes → stdout bytes
│   ├── series_source.*      随机序列数据源：random_series / wave_series / mixed_series
│   ├── series_transform.*   序列处理器：scale / normalize / moving_average / describe
│   ├── series_report.*      序列统计：summarize / histogram
│   ├── frame_source.*       表格数据源（演示 DataFrame 列 dtype）
│   ├── json_normalizer.*    JSON 扁平化适配器
│   ├── py_stats.*           统计模块
│   ├── shell_counter.*      宽松格式 Shell 消费者
│   ├── stream_market.*      实时行情源：tick / market_stream 两种入口写法
│   ├── stream_ma.*          实时链路上的均线变换（MA5 / MA10）
│   ├── stream_signals.*     实时信号源：红黄绿相位 + 8 路 LED 状态机
│   ├── candlestick_view.yaml K线渲染节点（runtime: render，画布内嵌 Canvas）
│   └── signal_board_view.yaml 电路板信号灯渲染节点（PCB + 三色交通灯）
├── scripts/                  用户在线编辑 / 新建的脚本落盘目录
└── web/                      index.html / style.css / app.js（原生单页，无构建）
    ├── streams.js            浏览器 JS 实时数据源注册表 + 前端变量树
    └── renderers.js          零依赖 Canvas K 线 / SVG+CSS 电路板灯板渲染器
```

---

## 3. 需求对照

| 需求 | 实现位置 |
| --- | --- |
| 3.1 Manifest（YAML/JSON、端口、runtime、params） | `modules/*.yaml`、`engine/mini_yaml.py`、`engine/core.py` |
| 按 transport 渲染端口形状（圆/方/菱形） | `web/app.js → shapeClass()` + `web/style.css .shape` |
| format_hint tooltip | `portRowHtml()` 的 `data-tip` + CSS `::after` |
| 连线兼容检查（字符串匹配 + 人工确认）+ 适配器占位节点 | `checkCompat()` / `showConfirm()` / `insertAdapter()` |
| 3.2 统一适配器接口 `RuntimeAdapter(ABC).execute()` | `engine/adapters.py` |
| PythonAdapter：`exec` 加载 + 抓局部变量 | `PythonAdapter` + `engine/entry.py`（`sys.settrace` 在 return 事件抓 `f_locals`） |
| ShellAdapter：`subprocess` + stdin/stdout bytes + 文本摘要变量树 | `ShellAdapter` |
| `ExecutionResult{outputs, variable_tree, meta}` | `engine/core.py → ExecutionResult` |
| 3.3 拓扑序 + 数据拼接规则 + 「边 X 需要适配器模块」中止 | `engine/scheduler.py → Scheduler.run()` / `transfer()` |
| 3.4 变量树 name/type_label/value_preview/children、dict/list 递归、DataFrame 列 dtype | `engine/var_tree.py`；右侧「输入/输出变量树」标签页 |
| 3.5 分类模块库 + runtime 角标 + 拖拽 + 双击参数面板 + 四个标签页 | `web/app.js`、`web/index.html` |
| 适配器占位节点可双击编辑脚本 | `openScriptEditor()` + `POST /api/script`（写回 `scripts/<节点>.py`） |
| **逐节点选 Python 环境**（内置 in-process / 外部 venv·conda 子进程） | `engine/environments.py`、`engine/runner.py`、`engine/py_subprocess.py`、`/api/envs` |
| **脚本文件切换 + 在线编辑 / 新建** | `GET /api/scripts`、`GET/POST /api/script`、前端 `execConfigHtml()` |
| **任选顶层函数作为入口 + 按形参名自动注入** | `engine/entry.py → list_functions() / find_entry() / build_call()` |
| **实时数据流：后端 tick 线程 + SSE 逐帧推送** | `engine/stream.py`、`POST /api/stream/start`、`GET /api/stream` |
| **双通道切换：Python 脚本 / 浏览器 JS 生成器** | Manifest `source_mode` + `web/streams.js` + 节点面板「数据来源」 |
| **画布内嵌实时渲染节点（K 线 / PCB 信号灯）** | `web/renderers.js`、`web/app.js → dispatchFrame()`、`runtime: render` |
| 实时帧仍带调试器式变量树 | 帧字段 `tree`（每 3 帧一次快照），右侧「输出变量树」直接可读 |

### transport 拼接矩阵（`transfer()`）

| 上游 \ 下游 | `memory_object` | `bytes` | `file_path` |
| --- | --- | --- | --- |
| `memory_object` | **同进程直接传对象**（零拷贝引用） | **中止**：`边 X 需要适配器模块` | 写临时文件，传路径 |
| `bytes` | JSON 反序列化成对象 | 直传字节 | 写临时文件，传路径 |
| `file_path` | 读回 + JSON 反序列化 | 读回字节 | 直传路径 |

本版序列化用 JSON + UTF-8，后续可替换为 Arrow IPC（`engine/payload.py → to_bytes()` 与 `scheduler.py → transfer()` 是替换点）。

> 若下游节点选择的是**外部 Python 环境**，两个 `memory_object` 之间无法真的共享引用：调度器会把载荷经 JSON + base64 桥接送进子进程，由 `runner.py` 还原成原生 Python 对象后再调用入口函数。此时边日志会写明「跨解释器桥接」，变量树仍显示 `builtins.list` / `builtins.dict` / 列 dtype 等原生类型。

---

## 4. 验收操作（对应需求文档第 4 节）

1. **三节点闭环**：点「验收示例」放下 `Python 数据源 → Python 适配器 → Shell 消费者`（打开页面默认显示的是实时演示，见第 6 节）。
2. **运行 + 变量树**：点「运行数据流」，三个模块按拓扑序 `n1 → n2 → n3` 执行；依次点节点，右侧「输入变量树」「输出变量树」可展开看 `name / type_label / value_preview`。
3. **类型标注**：选中 `py_source`，输出变量树展开 `data → mixed`，可见 `builtins.list`，其子元素为 `builtins.int` / `builtins.str` 交替；`data → frame` 展开后每列的 `type_label` 是 `int64 / float64 / object`。
4. **不兼容 → 适配器占位提示**：选中中间的 `py_adapter`，在「参数」标签页把输出端口的 `format_hint` 从 `text:lines` 改成 `json:raw`（回车或失焦）。连线立刻变成橙色虚线，中点出现 **「⚠ 需要适配器占位」** 徽章；点击徽章即插入一个可编辑的适配器占位节点；运行时后端也会给出 warning。
   - 另一种复现：清空画布，直接把 `py_source` 的输出连到 `shell_consumer` 的输入 → 弹出人工确认框提示 `memory_object → bytes` 冲突。
5. **执行摘要**：右侧「执行摘要」标签页列出每个模块的 `runtime 标签 / 耗时 / 退出码 / stderr_tail`，以及每条边采用的拼接规则和抓取到的局部变量。
6. **选环境 / 选脚本 / 选函数**：点「序列示例」→ 选中第一个节点 → 在「执行配置」区把入口函数从 `random_series` 改成 `mixed_series` → 运行，输出变量树里出现 `builtins.int` / `builtins.str` 交替。选中中间节点可看到它用的是另一个 Python 版本，执行摘要里的 runtime 标签会标 `subprocess`。
7. **实时流**：点「实时演示」→ K 线与信号灯同时按帧刷新；把某个数据源切成「浏览器 JS」，画面数据立刻换一条通道；工具条可改频率、启停；选中节点切到「输出变量树」能看到实时帧的类型标注。详细见第 6 节。

附加可试：画布上双击「适配器占位节点」→ 编辑脚本 → 保存，脚本写回 `scripts/`，再次运行即按新逻辑转换。

---

## 5. 选 Python 环境 / 脚本文件 / 入口函数

选中任意节点，「参数」标签页最上方就是 **执行配置** 区（Shell 节点只有环境选择，Python 节点三个下拉都有）。

| 项 | 行为 |
| --- | --- |
| **Python 环境** | 后端启动时自动探测项目内 venv、用户目录 conda / venv、PATH 上的 `python*` 与常见安装位置，并发校验版本。内置 `__inprocess__` 走 `exec` 同进程执行（原生对象零拷贝）；选外部解释器则起子进程，经 JSON + base64 桥接。登记过的自定义路径持久化在 `envs.json`。 |
| **脚本文件** | 下拉列出 `modules/*.py` 与 `scripts/*.py`，切换即刻改变该节点的 `entry`；「＋新建」或「编辑 / 查看脚本」打开在线编辑器，保存立即写盘（只允许写 `modules/` 与 `scripts/` 下的 `.py`），保存后自动刷新函数列表。 |
| **入口函数** | 用 AST 静态解析脚本的顶层函数（不执行脚本），下拉任选一个；显示签名与 docstring。`def execute(inputs)` 约定仍然兼容，未指定时优先 `execute`，若脚本只有一个顶层函数就自动用它。 |

**参数怎么传进自选函数**（`engine/entry.py → build_call()`，按形参名匹配，优先级从高到低）：

1. 形参名是 `inputs` → 整个上游输入字典；`params` / `PARAMS` / `config` → 参数面板的值；
2. 形参名命中某个输入端口名 → 该端口的载荷（例如 `def summarize(series)` 自动拿到 `series` 端口的 list）；
3. 形参名命中 params schema 的 key → 参数面板的值（例如 `def random_series(count, seed)` 的 `count` / `seed`）；
4. 有默认值 → 不传，用脚本默认；
5. 以上都不满足但有上游数据 → 传第一个上游值（适配 `def transform(seq)` 这类写法）。

返回值：字典若含输出端口名就按端口拆；若完全不含端口名且只有一个输出端口，整份字典自动归到该端口；其它类型（list / int / bytes…）归到第一个输出端口。

脚本里可直接使用 `PARAMS`（参数面板）与 `Frame`（零依赖表格，装了 pandas 时真实 DataFrame 走同一套变量树规则）。外部环境下 `runner.py` 会把 `base_dir` 加入 `sys.path`，因此也能 `import` 该环境里安装的第三方库。

新增 API：

```
GET  /api/envs[?refresh=1]     可用解释器列表（id / label / version / exe / kind）
POST /api/envs  {"path": "..."}         登记自定义解释器
GET  /api/scripts              所有脚本 + 每个脚本的顶层函数与签名
GET  /api/script?path=...      脚本源码 + 函数列表
POST /api/script {"path": "...", "script": "..."}   保存 / 另存脚本
```

Manifest 可用 `entry_fn` 声明默认入口函数，例如 `modules/series_source.yaml` 里的 `entry_fn: random_series`；画布上的实例通过 `env_id` / `entry_fn` 覆盖。

---

## 6. 实时流：K 线图与电路板信号灯

### 两种数据源通道

| | 后端 Python（默认） | 浏览器 JS |
| --- | --- | --- |
| 数据在哪产生 | `modules/stream_*.py` 由后台 tick 线程真实 `exec` 执行 | `web/streams.js` 的生成器，浏览器 `setInterval` |
| 怎么到前端 | SSE（`GET /api/stream?run_id=…`，`event: frame`） | 直接调 `dispatchFrame()`，不出网 |
| 变量树 | 后端 `build_tree()` 生成，每 3 帧带一次快照 | `web/streams.js → treeOf()` 生成同构结构 |
| 适合 | 验证多语言模块、流上还能拼接 Python 变换节点 | 纯前端演示、断网也能跑、改 JS 即换数据 |

切换：选中数据源节点 → 右侧「实时流 · 双通道配置」→ 数据来源下拉。切换会自动重启推流；后端只保留**从 Python 源可达**的子图，所以把 K 线源切到 JS 后，挂在它下游的 `stream_ma` 不会继续空跑；两个源都切 JS 时后端完全不启动线程。

### 链路与帧

```
stream_market (source) ──quote──▶ stream_ma (transform) ──market──▶ candlestick_view (render)
stream_signals(source) ──packet──────────────────────────────────▶ signal_board_view (render)
```

每帧是同构 JSON：`{frame, ts, source, root, by_node, payload, tree, meta}`，`by_node[node_id][port]` 是各模块本帧输出。`runtime: render` 的节点不参与后端执行（一次性「运行数据流」也会被调度器自动跳过），只按入边从帧里取数据重绘。流链路上的 transport 统一 `memory_object`，同进程内直传原生对象，只有发往浏览器那一步做 JSON 化。

### Python 流脚本怎么写

```python
# modules/my_stream.py —— 下面三种写法任选，入口函数可在面板里切换
def tick(frame, state, PARAMS=None):
    """每帧调用；state 是引擎跨帧维护的 dict，原地修改即可保存状态"""
    state["n"] = state.get("n", 0) + 1
    return {"candles": [...], "price": ...}   # 返回 dict，按输出端口名组织

def stream(PARAMS=None):
    """生成器写法：状态留在闭包里，每帧 yield 一包"""
    while True:
        yield {...}

def execute(inputs):
    """约定写法也兼容；一次性「运行数据流」时会注入 frame=0 / state={}"""
    return {...}
```

`frame`（帧号）、`state`（跨帧 dict）、`ts`、`interval_ms`、`PARAMS`（参数面板）与输入端口名都会按形参名自动注入。Manifest 里用 `mode: stream` 加 `stream_role: source|transform|render` 声明。

### 渲染节点

`render_type: candlestick` 用 Canvas 手绘蜡烛 / 影线 / MA5 / MA10 / 成交量 / 价格轴 / 最新价标签，**红涨绿跌**；`render_type: signal_board` 是深墨绿 PCB 板配金色走线，左侧红黄绿交通灯按相位状态机点亮，右侧 8 路 LED 支持 `on / off / blink / fault` 与电压显示。渲染器在 `web/renderers.js`，面板参数（`show_volume` / `show_ma` / `lanes`）下一帧即生效。

加第三种实时视图：在 `renderers.js → ensure()/draw()` 加分支，在 `modules/` 放一份 `runtime: render` 的 Manifest 指定新 `render_type`，刷新即可拖出来。加 JS 数据源：在 `web/streams.js` 的 `REG` 里注册一个 key，节点面板的「JS 生成器」下拉会自动列出。

### 相关 API

| 接口 | 作用 |
| --- | --- |
| `POST /api/stream/start` | 传 `{nodes, edges, interval_ms}`，启动 tick 线程并返回 `run_id` |
| `GET /api/stream?run_id=…` | SSE 长连接，`event: frame` 推帧、`event: end` 结束 |
| `GET /api/stream/status` | 帧号、fps、间隔、订阅者数、最近错误 |
| `POST /api/stream/stop` | 停止并通知所有 SSE 订阅者 |

订阅者队列上限 6 帧，积压时丢旧帧保实时性；连续 20 秒无订阅者自动收掉 tick 线程，页面卸载时也会用 `sendBeacon` 通知停止。

---

## 7. 怎么改（模板化扩展）

**加一个模块**：在 `modules/` 放一份 `.yaml` Manifest 和一个脚本，刷新页面即出现在左侧模块库，无需改任何前后端代码。

```yaml
id: my_module
name: 我的模块
category: 转换与适配
runtime: python
entry: modules/my_module.py
entry_fn: generate              # 可选：指定默认入口函数，不写则找 execute / 唯一顶层函数
inputs:
  - name: data
    transport: memory_object
    format_hint: "json:records"
    accepts: ["json:records", "any"]
outputs:
  - name: result
    transport: memory_object
    format_hint: "json:flat"
params:
  - key: limit
    type: int
    default: 10
    label: 条数上限
```

```python
# modules/my_module.py —— 函数名随意，形参按名字自动注入
def generate(limit=10):
    """生成一个序列"""
    return [{"id": i, "label": f"row-{i}"} for i in range(int(limit))]

# 也可以保留约定写法，兼容旧模块
def execute(inputs):
    return {"result": generate(int(PARAMS.get("limit", 10)))}
```

在节点面板里切换「脚本文件」或「入口函数」即可改执行逻辑，无需改前端代码；`PARAMS` 与 `Frame` 由适配器注入。

**加一种 runtime**：在 `engine/adapters.py` 继承 `RuntimeAdapter` 实现 `execute()`，注册进 `_REGISTRY`；`mcp` / `http` 目前由 `UnsupportedAdapter` 给出明确提示。

**加 shell 模块**：`entry` 里用 `{python}` 占位符指代解释器（跨平台，且会跟随节点所选环境），参数经环境变量 `DATAFLOW_PARAMS`（JSON）传入，stdin 收字节、stdout 写字节。

**换序列化**：改 `engine/payload.py → to_bytes()` / `encode_transport()` 与 `scheduler.py → transfer()`。

---

## 8. 边界与安全

- 本版按需求文档**不做**：AI 生成 MCP 工具、模板市场、多人协作、云端部署。
- `mcp` / `http` runtime 留待后续迭代；`file_path` transport 已实现基本读写但未做生命周期管理。
- 服务只绑定 `127.0.0.1`，脚本由本机用户自己编写并执行；**不要暴露到公网**。实时流是本机演示用的单画布单线程模型，一个画布只允许一条活跃 SSE 流，启动新的会停掉旧的。
- 实时流节点固定用服务内置解释器执行（每帧起子进程会拖垮帧率）；逐节点选外部解释器仍用于一次性批处理链路。
- 只有 `engine/environments.py` 探测或登记过的解释器路径会被执行，前端传任意 `env_id` 会被拒绝（验证项 6 覆盖）；脚本保存限定在 `modules/` 与 `scripts/` 下的 `.py`，防路径穿越。
- 外部解释器以子进程执行、60s 超时，输入输出走 JSON + base64；自定义对象会退化成 `repr` 字符串，复杂对象建议换成 Arrow IPC 等协议。
- `scripts/_wire_*.bin` 是 `file_path` 边产生的临时文件，可随时删除。
- YAML 解析是零依赖子集实现（缩进 mapping / sequence、标量、内联列表、注释），不覆盖 YAML 全集；需要复杂语法时可改用 `.json` Manifest。
