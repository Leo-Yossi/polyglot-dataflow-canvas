/* DataFlow Studio — 前端逻辑（原生 JS，无构建、无依赖） */
(function () {
  'use strict';

  // ===================================================================== //
  // 状态
  // ===================================================================== //
  const S = {
    manifests: [], byId: {}, categories: [],
    envs: [], scripts: [],
    nodes: [], edges: [], results: {}, lastRun: null,
    selected: null, tab: 'params', online: false, running: false,
    seqN: 0, seqE: 0, seqP: 0,
    link: null, drag: null, filter: '',
    pendingConfirm: null, scriptTarget: null,
    // 实时流：后端 SSE（python 源）+ 浏览器定时器（js 源）双通道
    stream: { active: false, runId: null, es: null, interval: 500, frame: 0,
              fps: 0, frameTimes: [], timers: {}, sources: {} },
    viewState: {},   // node_id -> 最近一帧的渲染数据
  };

  const DEFAULT_PLACEHOLDER_SCRIPT =
`# 适配器占位节点：一等公民模块，可自由改写
# 输入端口 in，输出端口 out；返回值按输出端口名组织
def execute(inputs):
    payload = next(iter(inputs.values()), None)
    if isinstance(payload, bytes):
        return {"out": payload}
    if isinstance(payload, str):
        return {"out": payload.encode("utf-8")}
    import json
    return {"out": json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")}
`;

const NEW_SCRIPT_TEMPLATE =
`# 新建脚本：写任意顶层函数，然后在节点面板的「入口函数」下拉里选择
# 形参名会按上游端口名 / params 参数名自动注入；PARAMS 可直接读取参数面板
def execute(inputs):
    result = next(iter(inputs.values()), None)
    return {"out": result}
`;

  const $ = (id) => document.getElementById(id);
  const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const clone = (v) => JSON.parse(JSON.stringify(v));
  const uid = (p) => { if (p === 'n') return 'n' + (++S.seqN); if (p === 'e') return 'e' + (++S.seqE); return 'ph' + (++S.seqP); };
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const getNode = (id) => S.nodes.find((n) => n.node_id === id);
  const getEdge = (id) => S.edges.find((e) => e.id === id);
  const portOf = (node, side, name) => (side === 'out' ? node.outputs : node.inputs).find((p) => p.name === name);

  // ===================================================================== //
  // 通用 UI
  // ===================================================================== //
  function toast(msg, kind) {
    const el = document.createElement('div');
    el.className = 'toast ' + (kind || '');
    el.innerHTML = esc(msg);
    $('toastArea').appendChild(el);
    setTimeout(() => { el.style.opacity = '0'; el.style.transition = 'opacity .25s'; }, 3800);
    setTimeout(() => el.remove(), 4150);
  }

  function setStatus(text, right) {
    $('statusLeft').textContent = text;
    if (right) $('statusRight').textContent = right;
  }

  function updateStats() {
    $('statNodes').textContent = '节点 ' + S.nodes.length;
    $('statEdges').textContent = '连线 ' + S.edges.length;
    const warn = S.edges.filter((e) => e.incompatible && !e.via_adapter).length;
    $('statRuntime').textContent = warn ? ('⚠ ' + warn + ' 处需适配器') : '拓扑就绪';
    $('statRuntime').style.color = warn ? '#a55c0d' : '';
  }

  function openModal(id) { $(id).classList.remove('hidden'); }
  function closeModal(id) { $(id).classList.add('hidden'); }

  // ===================================================================== //
  // 格式兼容（与后端 scheduler.check_format 保持同一套简单字符串匹配规则）
  // ===================================================================== //
  function checkCompat(up, down) {
    const accepts = (down.accepts && down.accepts.length ? down.accepts : [down.format_hint]).filter(Boolean);
    if (!accepts.length || accepts.includes('any') || accepts.includes('*') || up.format_hint === 'any') {
      return { ok: true, msg: '格式提示兼容（any / 未约束）' };
    }
    if (accepts.includes(up.format_hint)) {
      return { ok: true, msg: '格式提示匹配：' + up.format_hint };
    }
    for (const pat of accepts) {
      const re = new RegExp('^' + pat.replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*') + '$');
      if (re.test(up.format_hint)) return { ok: true, msg: '格式提示通配匹配：' + up.format_hint + ' ~ ' + pat };
    }
    return {
      ok: false,
      msg: "上游 format_hint='" + up.format_hint + "' 不在下游可接受列表 [" + accepts.join(', ') + '] 中',
    };
  }

  function transportBlocked(up, down) {
    return up.transport === 'memory_object' && down.transport === 'bytes';
  }

  // ===================================================================== //
  // 模块库
  // ===================================================================== //
  function renderLibrary() {
    const q = S.filter.trim().toLowerCase();
    const groups = {};
    S.manifests.forEach((m) => {
      if (q && !(m.name + ' ' + m.id + ' ' + m.runtime + ' ' + m.category).toLowerCase().includes(q)) return;
      (groups[m.category] ||= []).push(m);
    });
    let html = '';
    Object.keys(groups).forEach((cat) => {
      html += '<div class="lib-group"><div class="lib-group-title">' + esc(cat) + '</div>';
      groups[cat].forEach((m) => {
        html += '<div class="mod-card" draggable="true" data-mid="' + esc(m.id) + '" title="拖到画布创建节点">' +
          '<div class="mod-card-top"><span class="mod-name">' + esc(m.name) + '</span>' +
          '<span class="rt rt-' + esc(m.runtime) + '">' + esc(m.runtime) + '</span></div>' +
          '<div class="mod-desc">' + esc(m.description) + '</div>' +
          '<div class="mod-ports">in ' + m.inputs.length + ' · out ' + m.outputs.length +
          ' · ' + m.outputs.map((p) => p.transport).join('/') + '</div></div>';
      });
      html += '</div>';
    });
    if (!html) html = '<div class="tree-empty">没有匹配的模块</div>';
    $('libList').innerHTML = html;
    $('libList').querySelectorAll('.mod-card').forEach((card) => {
      card.addEventListener('dragstart', (e) => {
        e.dataTransfer.setData('text/plain', card.dataset.mid);
        e.dataTransfer.effectAllowed = 'copy';
      });
      card.addEventListener('dblclick', () => addNode(card.dataset.mid, 60 + S.nodes.length * 24, 90 + S.nodes.length * 22));
    });
  }

  // ===================================================================== //
  // 节点
  // ===================================================================== //
  function mkNode(mid, x, y) {
    const m = S.byId[mid];
    if (!m) return null;
    return {
      node_id: uid('n'), manifest_id: mid, name: m.name, runtime: m.runtime, entry: m.entry,
      inputs: clone(m.inputs), outputs: clone(m.outputs),
      params: Object.fromEntries((m.params || []).map((p) => [p.key, p.default])),
      script: null, is_placeholder: false,
      env_id: '__inprocess__', entry_fn: m.entry_fn || 'execute',
      mode: m.mode || 'batch', stream_role: m.stream_role || '',
      render_type: m.render_type || '', source_mode: m.source_mode || 'python',
      js_generator: m.js_generator || '', interval_ms: m.interval_ms || 600,
      x, y, _manifest: m,
    };
  }

  function addNode(mid, x, y) {
    const n = mkNode(mid, x, y);
    if (!n) return;
    if (S.stream.active) { stopStream(true); toast('画布结构已变化，实时流已停止', 'warn'); }
    S.nodes.push(n);
    S.selected = n.node_id;
    renderAll();
    toast('已添加模块：' + n.name + '（' + n.runtime + '）');
  }

  function shapeClass(port) {
    return port.transport === 'bytes' ? 'square' : port.transport === 'file_path' ? 'diamond' : 'circle';
  }

  function tipText(p, side) {
    return p.name + '  ·  transport: ' + p.transport + '  ·  format_hint: ' + p.format_hint +
      (side === 'in' && p.accepts && p.accepts.length ? '  ·  accepts: ' + p.accepts.join(', ') : '');
  }

  function portRowHtml(p, side) {
    const shape = '<span class="shape ' + shapeClass(p) + '" data-transport="' + esc(p.transport) + '"></span>';
    const name = '<span class="port-name">' + esc(p.name) + '</span>';
    return '<div class="port-row" data-side="' + side + '" data-port="' + esc(p.name) +
      '" data-tip="' + esc(tipText(p, side)) + '">' +
      (side === 'in' ? shape + name : name + shape) + '</div>';
  }

  const isViewNode = (n) => !!(n && (n.stream_role === 'render' || n.runtime === 'render'));
  const isStreamNode = (n) => !!(n && n.mode === 'stream');

  function viewNodeHtml(n, state) {
    const st = S.viewState[n.node_id] || {};
    return '<div class="node ' + state + (n.node_id === S.selected ? ' selected' : '') +
      ' view-node ' + (n.render_type === 'candlestick' ? 'candlestick' : 'board') +
      (S.stream.active ? ' streaming' : '') +
      (S.link && S.link.from !== n.node_id ? ' linking' : '') +
      '" data-id="' + esc(n.node_id) + '" style="left:' + n.x + 'px;top:' + n.y + 'px">' +
      '<div class="node-head"><span class="rt rt-render">VIEW</span>' +
      '<span class="node-title" title="' + esc(n.name) + '">' + esc(n.name) + '</span>' +
      '<span class="node-status"></span></div>' +
      '<div class="node-sub">' + esc(n.manifest_id + ' · ' + n.render_type + ' · render') + '</div>' +
      '<div class="view-ports"><div class="port-col in">' +
      (n.inputs.length ? n.inputs.map((p) => portRowHtml(p, 'in')).join('') : '') +
      '</div></div>' +
      '<div class="view-body"><div class="view-host" data-view="' + esc(n.node_id) + '"></div></div>' +
      '<div class="node-foot"><span class="fmt-chip">' + esc(n.render_type) + '</span>' +
      '<span class="node-tag stream">帧 ' + (st.frame || 0) + '</span></div></div>';
  }

  function renderNodes() {
    $('nodesLayer').innerHTML = S.nodes.map((n) => {
      const r = S.results[n.node_id];
      const state = r ? (r.error ? 'err' : 'ok') : '';
      if (isViewNode(n)) return viewNodeHtml(n, state);
      const rt = n.is_placeholder ? 'placeholder' : n.runtime;
      const envInfo = S.envs.find((e) => e.id === n.env_id);
      const envTag = envInfo && envInfo.kind === 'external'
        ? '<span class="node-tag env" title="' + esc(envInfo.label + ' · ' + envInfo.exe) + '">PY ' + esc(envInfo.version) + '</span>'
        : '';
      const scriptName = n.runtime === 'python'
        ? ((n.entry || '').split('/').pop() || (n.script ? '内联脚本' : '未指定脚本'))
        : 'shell 命令';
      const sub = n.manifest_id + ' · ' + scriptName +
        (n.runtime === 'python' && n.entry_fn ? ' · ' + n.entry_fn + '()' : '');
      return '<div class="node ' + state + (n.node_id === S.selected ? ' selected' : '') +
        (n.is_placeholder ? ' placeholder' : '') + (S.link && S.link.from !== n.node_id ? ' linking' : '') +
        '" data-id="' + esc(n.node_id) + '" style="left:' + n.x + 'px;top:' + n.y + 'px">' +
        '<div class="node-head">' +
        '<span class="rt rt-' + esc(rt) + '">' + (n.is_placeholder ? 'ADAPT' : n.runtime === 'python' ? 'PY' : 'SH') + '</span>' +
        '<span class="node-title" title="' + esc(n.name) + '">' + esc(n.name) + '</span>' +
        '<span class="node-status"></span></div>' +
        '<div class="node-sub" title="' + esc(sub) + '">' + esc(sub) + '</div>' +
        '<div class="node-body">' +
        '<div class="port-col in">' + (n.inputs.length ? n.inputs.map((p) => portRowHtml(p, 'in')).join('') : '<div class="port-row"><span class="port-name" style="color:#b3bfd0">无输入</span></div>') + '</div>' +
        '<div class="port-col out">' + (n.outputs.length ? n.outputs.map((p) => portRowHtml(p, 'out')).join('') : '<div class="port-row"><span class="port-name" style="color:#b3bfd0">无输出</span></div>') + '</div>' +
        '</div><div class="node-foot">' +
        n.outputs.map((p) => '<span class="fmt-chip out">' + esc(p.format_hint) + '</span>').join('') +
        (n.is_placeholder ? '<span class="node-tag">双击编辑脚本</span>' : '') +
        envTag +
        '</div></div>';
    }).join('');
    // 节点 DOM 被重建时，用最近一帧重绘内嵌视图，避免画面闪空
    requestAnimationFrame(repaintViews);
  }

  // ===================================================================== //
  // 连线
  // ===================================================================== //
  function portCenter(nodeId, side, portName) {
    const rows = document.querySelectorAll('#nodesLayer .node[data-id="' + nodeId + '"] .port-row');
    let el = null;
    rows.forEach((row) => {
      if (row.dataset.side === side && row.dataset.port === portName) el = row.querySelector('.shape');
    });
    if (!el) return null;
    const base = $('canvasInner').getBoundingClientRect();
    const r = el.getBoundingClientRect();
    return { x: r.left + r.width / 2 - base.left, y: r.top + r.height / 2 - base.top };
  }

  function bezier(p1, p2) {
    const dx = Math.max(48, Math.abs(p2.x - p1.x) * 0.46);
    return 'M ' + p1.x + ' ' + p1.y + ' C ' + (p1.x + dx) + ' ' + p1.y + ', ' +
      (p2.x - dx) + ' ' + p2.y + ', ' + p2.x + ' ' + p2.y;
  }

  function renderWires() {
    let html = '';
    S.edges.forEach((e) => {
      const a = portCenter(e.from, 'out', e.from_port);
      const b = portCenter(e.to, 'in', e.to_port);
      if (!a || !b) return;
      const path = bezier(a, b);
      const upNode = getNode(e.from), downNode = getNode(e.to);
      const live = S.stream.active && isStreamNode(upNode) && isStreamNode(downNode);
      html += '<path class="wire-bg" d="' + path + '"/>' +
        '<path class="wire' + (e.incompatible ? ' warn' : '') + (live ? ' live' : '') + '" d="' + path + '"/>' +
        '<circle class="wire-dot' + (e.incompatible ? ' warn' : '') + '" cx="' +
        ((a.x + b.x) / 2) + '" cy="' + ((a.y + b.y) / 2) + '" r="3.4"/>';
    });
    $('wirePaths').innerHTML = html;
    $('badgeLayer').innerHTML = S.edges.filter((e) => e.incompatible && !e.via_adapter).map((e) => {
      const a = portCenter(e.from, 'out', e.from_port);
      const b = portCenter(e.to, 'in', e.to_port);
      if (!a || !b) return '';
      return '<div class="edge-badge" data-edge="' + esc(e.id) + '" style="left:' +
        ((a.x + b.x) / 2) + 'px;top:' + ((a.y + b.y) / 2) + 'px" title="点击在这条连线上插入适配器占位节点">' +
        '<span class="dot"></span>⚠ 需要适配器占位</div>';
    }).join('');
  }

  function revalidateEdges() {
    S.edges.forEach((e) => {
      const src = getNode(e.from), dst = getNode(e.to);
      if (!src || !dst) { e.incompatible = false; return; }
      const up = portOf(src, 'out', e.from_port), down = portOf(dst, 'in', e.to_port);
      if (!up || !down) { e.incompatible = false; return; }
      const fmt = checkCompat(up, down);
      e.incompatible = !fmt.ok || (transportBlocked(up, down) && !e.via_adapter);
      e.reason = fmt.ok ? (transportBlocked(up, down) ? 'memory_object → bytes 需要适配器模块' : '') : fmt.msg;
    });
    updateStats();
  }

  // ===================================================================== //
  // 兼容性确认 + 适配器占位节点
  // ===================================================================== //
  function tryConnect(srcId, srcPort, dstId, dstPort) {
    if (srcId === dstId) { toast('不能把节点连到自己', 'warn'); return; }
    const src = getNode(srcId), dst = getNode(dstId);
    if (!src || !dst) return;
    const up = portOf(src, 'out', srcPort), down = portOf(dst, 'in', dstPort);
    if (!up || !down) return;
    if (S.edges.some((e) => e.to === dstId && e.to_port === dstPort)) {
      toast('该输入端口已有连线，请先删除原连线', 'warn'); return;
    }
    const fmt = checkCompat(up, down);
    const blocked = transportBlocked(up, down);
    if (fmt.ok && !blocked) {
      S.edges.push({ id: uid('e'), from: srcId, from_port: srcPort, to: dstId, to_port: dstPort, incompatible: false });
      revalidateEdges(); renderAll();
      toast('连线已建立：' + up.format_hint + ' → ' + down.format_hint + ' · ' + up.transport + ' → ' + down.transport, 'ok');
      return;
    }
    // 需求 3.1：简单字符串匹配 + 人工确认
    showConfirm(up, down, fmt, blocked, (choice) => {
      if (choice === 'cancel') return;
      const edge = { id: uid('e'), from: srcId, from_port: srcPort, to: dstId, to_port: dstPort, incompatible: false };
      S.edges.push(edge);
      if (choice === 'adapter') {
        insertAdapter(edge);
        toast('已在连线上插入适配器占位节点，双击它可编辑转换脚本', 'ok');
      } else {
        edge.incompatible = true;
        edge.reason = fmt.ok ? 'memory_object → bytes 需要适配器模块' : fmt.msg;
        toast('已按人工确认直连；运行可能被调度器中止，请查看执行摘要', 'warn');
      }
      revalidateEdges(); renderAll();
    });
  }

  function showConfirm(up, down, fmt, blocked, cb) {
    $('confirmBody').innerHTML =
      '<div>这条连线的两端声明不一致。按需求文档，本版采用「简单字符串匹配 + 人工确认」，' +
      '你可以直连，或让平台在连线上生成一个<strong>适配器占位节点</strong>（一等公民模块，可双击编辑脚本）。</div>' +
      '<div class="compare">' +
      '<div class="compare-box"><div class="cap">上游输出</div><div class="val">' + esc(up.name) + ' · ' + esc(up.transport) + '<br>format_hint: ' + esc(up.format_hint) + '</div></div>' +
      '<div class="compare-arrow">→</div>' +
      '<div class="compare-box"><div class="cap">下游输入</div><div class="val">' + esc(down.name) + ' · ' + esc(down.transport) + '<br>format_hint: ' + esc(down.format_hint) + '<br>accepts: ' + esc((down.accepts && down.accepts.length ? down.accepts : [down.format_hint]).join(', ')) + '</div></div>' +
      '</div>' +
      '<div class="notice">' + (blocked
        ? '⚠ transport 冲突：memory_object → bytes。按 3.3 数据拼接规则，调度器会中止并报「边 X 需要适配器模块」。建议插入适配器。'
        : '⚠ ' + esc(fmt.msg) + '。格式不强制校验，但建议插入适配器或人工确认。') + '</div>';
    $('confirmFoot').innerHTML =
      '<button class="btn ghost" data-act="cancel">取消</button>' +
      '<button class="btn ghost" data-act="direct">仍要直连</button>' +
      '<button class="btn primary" data-act="adapter">插入适配器占位节点</button>';
    S.pendingConfirm = cb;
    openModal('confirmModal');
  }

  function insertAdapter(edge) {
    const src = getNode(edge.from), dst = getNode(edge.to);
    const up = portOf(src, 'out', edge.from_port), down = portOf(dst, 'in', edge.to_port);
    const ph = {
      node_id: uid('ph'), manifest_id: '__adapter_placeholder__',
      name: '适配器占位节点', runtime: 'python', entry: '',
      inputs: [{ name: 'in', transport: up.transport, format_hint: up.format_hint, accepts: ['any'], description: '承接上游' }],
      outputs: [{ name: 'out', transport: down.transport, format_hint: down.format_hint, accepts: [], description: '适配下游' }],
      params: {}, script: DEFAULT_PLACEHOLDER_SCRIPT, is_placeholder: true,
      env_id: '__inprocess__', entry_fn: 'execute',
      x: clamp(Math.round((src.x + dst.x) / 2), 10, 1500),
      y: Math.max(src.y, dst.y) + 175,
      _manifest: { id: '__adapter_placeholder__', name: '适配器占位节点', params: [], description: '用户可编辑的转换模块' },
    };
    S.nodes.push(ph);
    S.edges = S.edges.filter((e) => e.id !== edge.id);
    S.edges.push({ id: uid('e'), from: edge.from, from_port: edge.from_port, to: ph.node_id, to_port: 'in', via_adapter: ph.node_id, incompatible: false });
    S.edges.push({ id: uid('e'), from: ph.node_id, from_port: 'out', to: edge.to, to_port: edge.to_port, via_adapter: ph.node_id, incompatible: false });
  }

  function removeNode(id) {
    if (S.stream.active) { stopStream(true); toast('画布结构已变化，实时流已停止', 'warn'); }
    S.nodes = S.nodes.filter((n) => n.node_id !== id);
    S.edges = S.edges.filter((e) => e.from !== id && e.to !== id && e.via_adapter !== id);
    delete S.results[id];
    delete S.viewState[id];
    if (S.selected === id) S.selected = null;
    revalidateEdges(); renderAll();
  }

  function removeEdge(id) {
    S.edges = S.edges.filter((e) => e.id !== id);
    revalidateEdges(); renderAll();
  }

  // ===================================================================== //
  // 右侧检查器
  // ===================================================================== //
  function setTab(tab) {
    S.tab = tab;
    document.querySelectorAll('#tabs .tab').forEach((b) => b.classList.toggle('active', b.dataset.tab === tab));
    renderInspector();
  }

  function typeClass(label, kind) {
    const l = String(label || '');
    if (kind === 'column' || /^(int64|float64|object|bool|datetime|string)/.test(l)) return 't-dtype';
    if (kind === 'frame') return 't-frame';
    if (/bytes|bytearray/.test(l)) return 't-bytes';
    if (/list|tuple|set/.test(l)) return 't-list';
    if (/dict|Frame/.test(l)) return 't-dict';
    if (/str/.test(l)) return 't-str';
    if (/bool/.test(l)) return 't-bool';
    if (/float/.test(l)) return 't-float';
    if (/int/.test(l)) return 't-int';
    return '';
  }

  function treeHtml(nodes, depth) {
    if (!nodes || !nodes.length) return '';
    return nodes.map((n) => {
      const has = n.children && n.children.length;
      const open = depth < 1;
      return '<div class="tree-node"><div class="tree-row ' + (has ? '' : 'leaf') + '" data-tree-toggle>' +
        '<span class="tree-caret">' + (has ? (open ? '▾' : '▸') : '·') + '</span>' +
        '<span class="tree-name">' + esc(n.name) + '</span>' +
        '<span class="type-badge ' + typeClass(n.type_label, n.kind) + '">' + esc(n.type_label) + '</span>' +
        '<span class="tree-preview" title="' + esc(n.value_preview) + '">' + esc(n.value_preview) + '</span></div>' +
        (has ? '<div class="tree-children ' + (open ? '' : 'hidden') + '">' + treeHtml(n.children, depth + 1) + '</div>' : '') +
        '</div>';
    }).join('');
  }

  function treePanel(key, title) {
    const n = getNode(S.selected);
    const r = n ? S.results[n.node_id] : null;
    let body;
    if (!r) {
      body = '<div class="tree-empty">还没有本次运行的快照。<br>点击右上角「运行数据流」后，这里会显示 <b>' + esc(title) + '</b>。</div>';
    } else {
      const roots = (r.variable_tree && r.variable_tree[key]) || [];
      body = roots.length ? treeHtml(roots, 0) :
        '<div class="tree-empty">该模块本次运行没有' + esc(key === 'inputs' ? '输入端口数据' : '输出端口数据') + '。</div>';
      const locals = (r.variable_tree && r.variable_tree.locals) || [];
      if (key === 'outputs' && locals.length) {
        body += '<div class="section" style="margin-top:20px"><div class="section-title">调试器抓取的局部变量</div>' +
          treeHtml(locals, 0) + '</div>';
      }
      const summary = (r.variable_tree && r.variable_tree.text_summary) || [];
      if (summary.length) {
        body += '<div class="section" style="margin-top:16px"><div class="section-title">stdin / stdout 原始文本摘要</div>' +
          treeHtml(summary, 0) + '</div>';
      }
    }
    return '<div class="tree-toolbar"><button data-tree-all="1">展开全部</button><button data-tree-all="0">折叠全部</button></div>' +
      '<div class="section-title">' + esc(title) + ' · ' + (r ? esc(r.runtime_label) : '待运行') + '</div>' + body;
  }

  // ===================================================================== //
  // 执行配置：每个节点独立选择 Python 环境 / 脚本文件 / 入口函数
  // ===================================================================== //
  function scriptItem(n) {
    return S.scripts.find((s) => s.path === n.entry) || null;
  }

  function usableFunctions(item) {
    return item ? (item.functions || []).filter((f) => f.name && !f.private && !f.async) : [];
  }

  function clipWidth(text, max) {
    let width = 0, out = '';
    for (const ch of String(text)) {
      const cw = /[\u4e00-\u9fa5]/.test(ch) ? 2 : 1;
      if (width + cw > max) return out + '…';
      width += cw; out += ch;
    }
    return out;
  }

  function shortEnvLabel(e) {
    return (e.kind === 'in-process' ? '● ' : '○ ') + clipWidth(e.label, 38);
  }

  function execConfigHtml(n) {
    const isPy = n.runtime === 'python';
    const envs = S.envs.length ? S.envs : [
      { id: '__inprocess__', label: '内置 · in-process', kind: 'in-process', version: '', exe: '', source: 'server' },
    ];
    const current = envs.find((e) => e.id === n.env_id) || envs[0];
    let html = '<div class="section"><div class="section-title">执行配置 · 环境 / 脚本 / 入口函数</div><div class="exec-box">';

    html += '<div class="field"><label class="exec-label">Python 环境 <span class="pill">' +
      (current.kind === 'in-process' ? '同进程' : '子进程') + '</span></label>' +
      '<select data-env>' + envs.map((e) =>
        '<option value="' + esc(e.id) + '"' + (e.id === n.env_id ? ' selected' : '') +
        ' title="' + esc(e.label + ' · ' + e.exe) + '">' + esc(shortEnvLabel(e)) + '</option>').join('') + '</select></div>' +
      '<div class="exec-meta">' + (current.kind === 'in-process'
        ? '内置解释器：exec 直接执行，<b>原生对象零拷贝</b>，变量树保留 list / dict / Frame 等原始类型'
        : '外部解释器 <b>' + esc(current.version) + '</b>：子进程执行，<b>JSON + base64 桥接</b>，可用该环境里的第三方库') +
      '</div>' +
      '<div class="custom-env"><input id="customEnvPath" type="text" placeholder="自定义解释器绝对路径">' +
      '<button class="mini-btn" data-act="addEnv">登记环境</button></div>';

    if (isPy) {
      const item = scriptItem(n);
      const inline = !item;
      html += '<div class="field"><label class="exec-label">脚本文件 <span class="hint">' +
        S.scripts.length + ' 个可选</span></label><div class="inline-row"><select data-script>' +
        (inline ? '<option value="__inline__" selected>内联脚本 / 未落盘</option>' : '') +
        S.scripts.map((s) => '<option value="' + esc(s.path) + '"' + (s.path === n.entry ? ' selected' : '') +
          '>' + esc(s.path) + '</option>').join('') +
        '</select><button class="mini-btn" data-act="newScript" title="新建一个脚本文件">＋新建</button></div></div>';

      const funcs = usableFunctions(item);
      const chosen = funcs.some((f) => f.name === n.entry_fn)
        ? n.entry_fn : (item && item.default_fn) || (funcs[0] && funcs[0].name) || n.entry_fn;
      html += '<div class="field"><label class="exec-label">入口函数 <span class="hint">任选顶层函数</span></label>' +
        '<select data-fn>' +
        (funcs.length
          ? funcs.map((f) => '<option value="' + esc(f.name) + '"' + (f.name === chosen ? ' selected' : '') +
            '>' + esc(f.signature) + '</option>').join('')
          : '<option value="' + esc(n.entry_fn || 'execute') + '" selected>' + esc(n.entry_fn || 'execute') + '() · 内联脚本</option>') +
        '</select></div>';
      const active = funcs.find((f) => f.name === chosen) || funcs.find((f) => f.name === n.entry_fn);
      html += '<div class="fn-hint">' + (active
        ? esc(active.signature) + (active.doc ? ' <em>· ' + esc(active.doc) + '</em>' : '')
        : '保存脚本后这里会列出全部顶层函数') + '</div>' +
        '<div class="btn-row"><button class="btn ghost" data-act="editScript">编辑 / 查看脚本</button>' +
        '<button class="btn ghost" data-act="refreshScripts">刷新脚本列表</button></div>' +
        '<div class="exec-meta">形参名命中输入端口或 params key 时自动注入；也可用 <b>PARAMS</b> 读参数面板。</div>';
    } else {
      html += '<div class="field"><label>Shell 命令 · entry</label>' +
        '<input type="text" class="mono" data-entry value="' + esc(n.entry) + '"></div>' +
        '<div class="exec-meta">命令中的 <b>{python}</b> 会替换为所选环境的解释器路径。</div>';
    }
    return html + '</div></div>';
  }

  function streamConfigHtml(n) {
    const isSource = n.stream_role === 'source';
    const opts = window.StreamSources ? window.StreamSources.list() : [];
    let html = '<div class="section"><div class="section-title">实时流 · 双通道配置</div><div class="stream-box">';
    if (isSource) {
      html += '<div class="field"><label class="exec-label">数据来源 <span class="source-pill ' +
        (n.source_mode === 'js' ? 'js' : '') + '">' +
        (n.source_mode === 'js' ? '浏览器 JS' : '后端 Python') + '</span></label>' +
        '<select data-source-mode>' +
        '<option value="python"' + (n.source_mode !== 'js' ? ' selected' : '') + '>后端 Python · SSE 真实执行脚本</option>' +
        '<option value="js"' + (n.source_mode === 'js' ? ' selected' : '') + '>浏览器 JS · 本地定时器生成</option>' +
        '</select></div>' +
        '<div class="field"><label class="exec-label">JS 生成器 <span class="hint">' + opts.length +
        ' 个内置</span></label><select data-js-gen>' +
        (opts.length ? opts.map((o) => '<option value="' + esc(o.key) + '"' +
          (o.key === n.js_generator ? ' selected' : '') + ' title="' + esc(o.description) + '">' +
          esc(o.label) + '</option>').join('')
          : '<option value="' + esc(n.js_generator || '') + '">' + esc(n.js_generator || '未加载') + '</option>') +
        '</select></div>';
    }
    html += '<div class="kv"><span class="k">流角色</span><span class="v">' + esc(n.stream_role) + '</span></div>' +
      '<div class="kv"><span class="k">推流间隔</span><span class="v">' + esc(S.stream.interval) + ' ms · 工具条统一设置</span></div>' +
      '<div class="stream-meta">' +
      (isSource && n.source_mode === 'js'
        ? '当前帧在浏览器里由 <code>web/streams.js</code> 的 <b>' + esc(n.js_generator || 'market') +
          '</b> 生成器产生，不经过后端；直接改该文件即可换数据源逻辑。'
        : '后端起 tick 线程真实执行 <code>' + esc(n.entry) + '</code> 的 <b>' + esc(n.entry_fn || 'tick') +
          '()</b>，经 SSE 推送；脚本里写 <code>def stream()</code> 生成器同样受支持。') +
      '</div></div></div>';
    return html;
  }

  function renderConfigHtml(n) {
    const st = S.viewState[n.node_id] || {};
    return '<div class="section"><div class="section-title">实时渲染配置</div><div class="render-box">' +
      '<div class="kv"><span class="k">渲染器</span><span class="v">' + esc(n.render_type) + '</span></div>' +
      '<div class="kv"><span class="k">最近帧</span><span class="v">frame ' + (st.frame || 0) + '</span></div>' +
      '<div class="kv"><span class="k">数据来源</span><span class="v">' + esc(st.source || '—') + '</span></div>' +
      '<div class="stream-meta" style="margin:9px 0 0">这类节点不在后端执行：帧到达时由 ' +
      '<code>web/renderers.js</code> 在画布内重绘。切到「输出变量树」可看最近一帧的原生类型标注；' +
      '下方参数改动会立即作用于下一帧。</div></div></div>';
  }

  function paramsPanel() {
    const n = getNode(S.selected);
    if (!n) return '<div class="placeholder-panel"><span class="big">◎</span>点击画布上的节点查看参数与端口声明<br>双击节点可直接打开本面板</div>';

    let html = '<div class="section"><div class="section-title">节点信息</div>' +
      '<div class="kv"><span class="k">实例 ID</span><span class="v">' + esc(n.node_id) + '</span></div>' +
      '<div class="kv"><span class="k">Manifest</span><span class="v">' + esc(n.manifest_id) + '</span></div>' +
      '<div class="kv"><span class="k">Runtime</span><span class="v">' + esc(n.runtime) + '</span></div>' +
      '<div class="kv"><span class="k">Entry</span><span class="v">' + esc(n.entry || '<内联脚本>') + '</span></div>' +
      '<div class="kv"><span class="k">入口函数</span><span class="v">' + esc(n.entry_fn || 'auto') + '()</span></div></div>';

    if (isViewNode(n)) {
      html += renderConfigHtml(n);
    } else {
      if (isStreamNode(n)) html += streamConfigHtml(n);
      html += execConfigHtml(n);
    }

    if (n.is_placeholder) {
      html += '<div class="notice">这是一等公民「适配器占位节点」：输入输出格式不同，内部做转换。双击节点或点下方按钮编辑脚本。</div>' +
        '<button class="btn primary" id="btnEditScript" style="width:100%;margin-bottom:18px">编辑转换脚本</button>';
    }

    const specs = (n._manifest && n._manifest.params) || [];
    if (specs.length) {
      html += '<div class="section"><div class="section-title">可配置参数 · params schema</div>';
      specs.forEach((spec) => {
        const v = n.params[spec.key];
        html += '<div class="field"><label>' + esc(spec.label || spec.key) +
          '<span class="hint">' + esc(spec.type) + '</span></label>';
        if (spec.type === 'bool') {
          html += '<label class="field-check"><input type="checkbox" data-param="' + esc(spec.key) + '"' +
            (v ? ' checked' : '') + '> 启用</label>';
        } else if (spec.type === 'int') {
          html += '<input type="number" class="mono" data-param="' + esc(spec.key) + '" value="' + esc(v) + '">';
        } else {
          html += '<input type="text" class="mono" data-param="' + esc(spec.key) + '" value="' + esc(v) + '">';
        }
        html += '</div>';
      });
      html += '</div>';
    }

    html += '<div class="section"><div class="section-title">端口声明 · manifest</div>' +
      '<div class="notice info">输出端口的 <b>format_hint</b> 可直接改写；改动会立即重新校验所有连线，并在不兼容的连线上生成「适配器占位」提示。</div>';
    [['in', n.inputs], ['out', n.outputs]].forEach(([side, ports]) => {
      ports.forEach((p, i) => {
        html += '<div class="port-edit"><div class="port-edit-head">' +
          '<span class="shape ' + shapeClass(p) + '" data-transport="' + esc(p.transport) + '"></span>' +
          esc(p.name) + '<span class="side">' + (side === 'in' ? 'INPUT' : 'OUTPUT') + ' · ' + esc(p.transport) + '</span></div>' +
          '<div class="field"><label>format_hint</label>' +
          (side === 'out'
            ? '<input type="text" class="mono" data-fmt="out" data-index="' + i + '" value="' + esc(p.format_hint) + '">'
            : '<input type="text" class="mono" value="' + esc(p.format_hint) + '" disabled>') + '</div>' +
          (side === 'in' ? '<div class="kv"><span class="k">accepts</span><span class="v">' + esc((p.accepts || []).join(', ') || '≈ 自身 format_hint') + '</span></div>' : '') +
          '</div>';
      });
    });
    html += '</div>';

    html += '<div class="section"><button class="btn ghost" id="btnDeleteNode" style="width:100%">删除此节点</button></div>';
    return html;
  }

  function summaryPanel() {
    const r = S.lastRun;
    if (!r) return '<div class="placeholder-panel"><span class="big">▤</span>尚未运行数据流。<br>点击「运行数据流」后，这里展示每个模块的耗时、退出码、runtime 标签、stderr 与边传输规则。</div>';

    let html = '<div class="summary-hero ' + (r.ok ? 'ok' : 'fail') + '"><div>' +
      '<div class="big">' + (r.ok ? '✓ 执行成功' : '✕ 执行未完成') + '</div>' +
      '<div class="small">run_id ' + esc(r.run_id) + ' · 总耗时 ' + esc(r.total_ms) + ' ms · 拓扑序 ' + esc((r.order || []).join(' → ')) + '</div></div></div>';

    if (r.error) html += '<div class="notice">' + esc(r.error) + '</div>';
    (r.warnings || []).forEach((w) => { html += '<div class="log-item warn">⚠ ' + esc(w) + '</div>'; });

    html += '<div class="section"><div class="section-title">模块执行摘要</div><table class="summary-table">' +
      '<thead><tr><th>节点</th><th>runtime 标签</th><th>耗时</th><th>退出码</th></tr></thead><tbody>';
    (r.order || []).forEach((id) => {
      const res = r.results[id];
      const n = getNode(id);
      if (!res) { html += '<tr><td>' + esc(id) + '</td><td colspan="3">未执行</td></tr>'; return; }
      const m = res.meta || {};
      html += '<tr><td>' + esc(n ? n.name : id) + '<br><span style="color:#8b9ab0">' + esc(id) + '</span></td>' +
        '<td class="mono">' + esc(res.runtime_label || m.runtime_label) + '</td>' +
        '<td class="mono">' + esc(m.duration_ms) + ' ms</td>' +
        '<td class="mono ' + (m.exit_code === 0 ? 'exit-ok' : 'exit-bad') + '">' + esc(m.exit_code) + '</td></tr>';
      if (res.error) html += '<tr><td colspan="4" style="color:#b33329">错误：' + esc(res.error) + '</td></tr>';
      if (m.stderr_tail) html += '<tr><td colspan="4"><div class="log-item">stderr_tail: ' + esc(String(m.stderr_tail).slice(0, 300)) + '</div></td></tr>';
      if (m.captured_locals && m.captured_locals.length) {
        html += '<tr><td colspan="4" class="mono">抓取局部变量: ' + esc(m.captured_locals.join(', ')) + '</td></tr>';
      }
      if (m.command) html += '<tr><td colspan="4" class="mono">command: ' + esc(m.command) + '</td></tr>';
    });
    html += '</tbody></table></div>';

    if ((r.edge_log || []).length) {
      html += '<div class="section"><div class="section-title">边 · 数据拼接规则（3.3）</div>';
      r.edge_log.forEach((e) => {
        html += '<div class="log-item ' + (e.format_compatible ? '' : 'warn') + '"><b>' + esc(e.from) + ' → ' + esc(e.to) + '</b><br>' +
          esc(e.rule) + '<br>' + esc(e.format_message) + '</div>';
      });
      html += '</div>';
    }
    return html;
  }

  function renderInspector() {
    const n = getNode(S.selected);
    $('inspectorNodeName').textContent = n ? n.name : '未选中';
    let html = '';
    if (S.tab === 'params') html = paramsPanel();
    else if (S.tab === 'inputs') html = treePanel('inputs', '输入变量树（只读）');
    else if (S.tab === 'outputs') html = treePanel('outputs', '输出变量树');
    else html = summaryPanel();
    $('tabBody').innerHTML = html;
  }

  function renderAll() {
    renderNodes();
    renderWires();
    renderInspector();
    updateStats();
    $('canvasEmpty').classList.toggle('hidden', S.nodes.length > 0);
    $('canvasHint').textContent = S.nodes.length
      ? '点击输出端口 → 点击输入端口连线 · 拖拽节点头部移动 · 双击节点打开面板'
      : '从左侧拖入模块，或点击「加载验收示例」';
    requestAnimationFrame(() => { renderWires(); repaintViews(); });
  }

  function renderNodeStatus() {
    document.querySelectorAll('#nodesLayer .node').forEach((el) => {
      const r = S.results[el.dataset.id];
      el.classList.remove('ok', 'err');
      if (r) el.classList.add(r.error ? 'err' : 'ok');
    });
  }

  // ===================================================================== //
  // 实时流：双通道（后端 Python SSE + 浏览器 JS 定时器）
  // ===================================================================== //
  function jsSourceNodes() {
    return S.nodes.filter((n) => isStreamNode(n) && n.stream_role === 'source' && n.source_mode === 'js');
  }

  function pySourceNodes() {
    return S.nodes.filter((n) => isStreamNode(n) && n.stream_role === 'source' && n.source_mode !== 'js');
  }

  function reachableIds(root) {
    const out = [root], queue = [root];
    while (queue.length) {
      const cur = queue.shift();
      S.edges.filter((e) => e.from === cur).forEach((e) => {
        if (out.indexOf(e.to) === -1) { out.push(e.to); queue.push(e.to); }
      });
    }
    return out;
  }

  // 从一帧里挑出该视图节点该画的那一包数据（兼容按端口包装 / 整包直出 / js 根兜底）
  function pickData(frame, view) {
    const byNode = frame.by_node || {};
    // 按渲染器要求的数据形状过滤：信号灯帧不能被喂给 K 线视图，反之亦然
    const want = view.render_type === 'candlestick' ? 'candles' : 'signals';
    const candidates = [];
    const edge = S.edges.find((e) => e.to === view.node_id);
    if (edge && byNode[edge.from]) {
      const p = byNode[edge.from];
      candidates.push(p[edge.from_port]);
      candidates.push(p);
      const keys = Object.keys(p);
      if (keys.length === 1) candidates.push(p[keys[0]]);
    }
    if (frame.root && byNode[frame.root]) {
      const p = byNode[frame.root];
      candidates.push(p);
      const keys = Object.keys(p);
      if (keys.length === 1) candidates.push(p[keys[0]]);
    }
    candidates.push(frame.payload);
    const fallback = [];
    for (const c of candidates) {
      if (c && typeof c === 'object' && !Array.isArray(c) && Array.isArray(c[want])) {
        // 优先用有内容的帧，避免上游空帧把画面刷成空白
        if (c[want].length) return c;
        fallback.push(c);
      }
    }
    return fallback[0] || null;
  }

  function repaintViews() {
    if (!window.Renderers) return;
    S.nodes.filter(isViewNode).forEach((n) => {
      const host = document.querySelector('.view-host[data-view="' + n.node_id + '"]');
      if (!host) return;
      window.Renderers.ensure(host, n);
      const st = S.viewState[n.node_id];
      if (st && st.data) window.Renderers.draw(host, n, st.data, st);
    });
  }

  function updateStreamStatus() {
    const el = $('streamStatus'), btn = $('btnStream');
    if (!el || !btn) return;
    if (S.stream.active) {
      el.className = 'stream-status on';
      el.innerHTML = '<i class="live-dot"></i>帧 ' + S.stream.frame + ' · ' + S.stream.fps.toFixed(1) + ' fps';
      btn.textContent = '■ 停止推流';
      btn.classList.add('stream-on');
    } else {
      el.className = 'stream-status';
      el.innerHTML = '<i class="live-dot"></i>实时流未启动';
      btn.textContent = '▶ 开始推流';
      btn.classList.remove('stream-on');
    }
  }

  function dispatchFrame(frame) {
    S.stream.frame = Math.max(S.stream.frame, frame.frame || 0);
    let hits = 0;
    S.nodes.filter(isViewNode).forEach((v) => {
      const data = pickData(frame, v);
      if (!data) return;
      hits++;
      const info = { frame: frame.frame, source: frame.source, fps: S.stream.fps, ts: frame.ts };
      S.viewState[v.node_id] = Object.assign({ data }, info);
      const host = document.querySelector('.view-host[data-view="' + v.node_id + '"]');
      if (host && window.Renderers) window.Renderers.draw(host, v, data, info);
      const tag = document.querySelector('.node[data-id="' + v.node_id + '"] .node-tag.stream');
      if (tag) tag.textContent = '帧 ' + frame.frame;
    });

    // 只统计真正驱动了画面的帧；用 2 秒时间窗口，避免多源并行时相邻帧间隔趋近 0
    if (hits) {
      const now = performance.now();
      const times = S.stream.frameTimes;
      times.push(now);
      while (times.length > 2 && now - times[0] > 2000) times.shift();
      if (times.length >= 2) {
        const span = (now - times[0]) / 1000;
        const inst = times.length / Math.max(span, 2.0);
        S.stream.fps = S.stream.fps ? S.stream.fps * 0.7 + inst * 0.3 : inst;
      }
    }

    // 帧里的变量树快照写回 S.results，右侧「输入/输出变量树」直接可读
    const tree = frame.tree || {};
    const meta = frame.meta || {};
    Object.keys(tree).forEach((id) => {
      S.results[id] = {
        runtime_label: frame.source === 'js'
          ? 'javascript · browser（实时帧）' : 'python · stream（实时帧）',
        variable_tree: tree[id],
        meta: {
          exit_code: 0, duration_ms: meta.avg_exec_ms || 0,
          runtime_label: frame.source === 'js' ? 'javascript · browser' : 'python · stream',
          entry_fn: 'tick', frame: frame.frame,
        },
        error: (meta.errors || []).join('; ') || null,
        outputs_preview: {},
      };
    });
    if (S.tab !== 'params' && tree[S.selected]) renderInspector();
    updateStreamStatus();
  }

  function jsPortsById(rootId) {
    const map = {};
    reachableIds(rootId).forEach((id) => {
      const n = getNode(id);
      map[id] = ((n && n.outputs) || []).map((p) => p.name);
    });
    return map;
  }

  function emitJsFrame(n) {
    const rec = S.stream.sources[n.node_id];
    if (!rec || !window.StreamSources) return;
    rec.frame += 1;
    const data = rec.gen.next(rec.frame, n.params || {});
    const ids = reachableIds(n.node_id);
    const frame = window.StreamSources.makeFrame(
      n, data, rec.frame, ids, rec.frame % 3 === 1, jsPortsById(n.node_id));
    frame.meta.interval_ms = S.stream.interval;
    frame.meta.fps = S.stream.fps;
    dispatchFrame(frame);
  }

  function startJsSources(interval) {
    S.stream.sources = {};
    S.stream.timers = {};
    if (!window.StreamSources) return;
    jsSourceNodes().forEach((n) => {
      const key = window.StreamSources.has(n.js_generator) ? n.js_generator : 'market';
      S.stream.sources[n.node_id] = { gen: window.StreamSources.create(key), frame: 0, key };
      emitJsFrame(n);
      S.stream.timers[n.node_id] = setInterval(() => emitJsFrame(n), interval);
    });
  }

  async function startStream() {
    if (!S.online) { toast('后端离线，无法启动实时流', 'err'); return; }
    if (S.stream.active) return;
    const interval = parseInt($('streamInterval').value, 10) || 500;
    S.stream.interval = interval;
    S.stream.frame = 0; S.stream.fps = 0; S.stream.frameTimes = [];

    let runId = null, backend = false;
    if (pySourceNodes().length) {
      try {
        const res = await fetch('/api/stream/start', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(Object.assign(buildPayload(), { interval_ms: interval })),
        });
        const d = await res.json();
        if (d.error) throw new Error(d.error);
        runId = d.run_id; backend = !!d.has_backend;
      } catch (err) {
        toast('后端流启动失败：' + err.message + '（JS 通道仍可运行）', 'warn');
      }
    }

    S.stream.active = true;
    S.stream.runId = runId;
    if (backend && runId && typeof EventSource !== 'undefined') {
      const es = new EventSource('/api/stream?run_id=' + encodeURIComponent(runId));
      es.addEventListener('frame', (ev) => {
        try { dispatchFrame(JSON.parse(ev.data)); } catch (e) { /* 忽略坏帧 */ }
      });
      es.addEventListener('end', () => es.close());
      S.stream.es = es;
    }
    startJsSources(interval);
    document.querySelectorAll('#nodesLayer .node').forEach((el) => el.classList.add('streaming'));
    renderWires();
    updateStreamStatus();
    const channels = [backend ? 'Python SSE' : '', jsSourceNodes().length ? '浏览器 JS' : ''].filter(Boolean);
    toast('实时流已启动 · ' + (channels.join(' + ') || '无数据源') + ' · ' + interval + ' ms/帧', 'ok');
    setStatus('实时推流中 · ' + (channels.join(' + ') || '无数据源') + ' · ' + interval + ' ms/帧',
      '选中节点可在「输出变量树」查看最近一帧');
  }

  async function stopStream(silent) {
    if (!S.stream.active) return;
    const runId = S.stream.runId;
    S.stream.active = false;
    S.stream.runId = null;
    if (S.stream.es) { S.stream.es.close(); S.stream.es = null; }
    Object.keys(S.stream.timers).forEach((k) => clearInterval(S.stream.timers[k]));
    S.stream.timers = {}; S.stream.sources = {}; S.stream.frameTimes = [];
    if (S.online) {
      try {
        await fetch('/api/stream/stop', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ run_id: runId }),
        });
      } catch (err) { /* 服务已断开时忽略 */ }
    }
    document.querySelectorAll('#nodesLayer .node').forEach((el) => el.classList.remove('streaming'));
    renderWires();
    updateStreamStatus();
    if (!silent) toast('实时流已停止，画布保留最后一帧画面');
  }

  function toggleStream() { if (S.stream.active) stopStream(); else startStream(); }

  async function restartStream(reason) {
    if (!S.stream.active) return;
    await stopStream(true);
    await startStream();
    if (reason) toast(reason + '，实时流已按新配置重启', 'ok');
  }

  async function loadStreamExample(kind, autoStart) {
    const need = kind === 'candle'
      ? ['stream_market', 'stream_ma', 'candlestick_view']
      : kind === 'signal' ? ['stream_signals', 'signal_board_view']
        : ['stream_market', 'stream_ma', 'candlestick_view', 'stream_signals', 'signal_board_view'];
    if (need.some((id) => !S.byId[id])) {
      toast('实时模块未加载，请确认 server.py 已重启后刷新页面', 'err'); return;
    }
    if (S.stream.active) await stopStream(true);
    S.nodes = []; S.edges = []; S.results = {}; S.lastRun = null; S.viewState = {};
    S.seqN = 0; S.seqE = 0; S.seqP = 0;

    if (kind === 'both' || kind === 'candle') {
      const a = mkNode('stream_market', 40, 76);
      const b = mkNode('stream_ma', 320, 76);
      const c = mkNode('candlestick_view', 600, 52);
      S.nodes.push(a, b, c);
      S.edges.push(
        { id: uid('e'), from: a.node_id, from_port: 'quote', to: b.node_id, to_port: 'candles', incompatible: false },
        { id: uid('e'), from: b.node_id, from_port: 'market', to: c.node_id, to_port: 'quote', incompatible: false });
      S.selected = a.node_id;
    }
    if (kind === 'both' || kind === 'signal') {
      const d = mkNode('stream_signals', 40, 470);
      const e = mkNode('signal_board_view', 320, 448);
      S.nodes.push(d, e);
      S.edges.push({ id: uid('e'), from: d.node_id, from_port: 'packet', to: e.node_id, to_port: 'packet', incompatible: false });
      if (kind === 'signal') S.selected = d.node_id;
    }
    S.tab = 'params';
    document.querySelectorAll('#tabs .tab').forEach((x) => x.classList.toggle('active', x.dataset.tab === 'params'));
    revalidateEdges(); renderAll();
    $('canvas').scrollTo({ left: 0, top: 0 });
    const label = kind === 'both' ? 'K 线 + 电路板信号灯双链路' : kind === 'candle' ? 'K 线实时链路' : '信号灯实时链路';
    toast('已加载 ' + label + '，数据源默认走后端 Python（可在节点面板切成浏览器 JS）', 'ok');
    setStatus(label + ' 就绪 · 点「开始推流」或按钮自动开始');
    if (autoStart !== false) await startStream();
  }

  // ===================================================================== //
  // 运行
  // ===================================================================== //
  function buildPayload() {
    return {
      nodes: S.nodes.map((n) => ({
        node_id: n.node_id, manifest_id: n.manifest_id, name: n.name, runtime: n.runtime,
        entry: n.entry, inputs: clone(n.inputs), outputs: clone(n.outputs),
        params: clone(n.params), script: n.script || null,
        is_placeholder: !!n.is_placeholder,
        env_id: n.env_id || '', entry_fn: n.entry_fn || '',
        mode: n.mode || 'batch', stream_role: n.stream_role || '',
        render_type: n.render_type || '', source_mode: n.source_mode || 'python',
        js_generator: n.js_generator || '', interval_ms: n.interval_ms || 600,
        x: n.x, y: n.y,
      })),
      edges: S.edges.map((e) => ({
        id: e.id, from: e.from, from_port: e.from_port, to: e.to, to_port: e.to_port,
        via_adapter: e.via_adapter || null,
      })),
    };
  }

  async function runFlow() {
    if (!S.online) { toast('后端离线：请在项目目录执行 python server.py 后刷新', 'err'); return; }
    if (!S.nodes.length) { toast('画布为空，请先添加模块', 'warn'); return; }
    if (S.running) return;
    S.running = true;
    $('btnRun').disabled = true;
    $('btnRun').innerHTML = '<span class="play">◌</span> 执行中…';
    setStatus('正在按拓扑序执行 ' + S.nodes.length + ' 个模块…');
    document.querySelectorAll('#nodesLayer .node').forEach((el) => el.classList.add('running'));
    try {
      const res = await fetch('/api/run', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(buildPayload()),
      });
      const data = await res.json();
      S.lastRun = data;
      S.results = data.results || {};
      renderNodeStatus();
      if (data.ok) {
        toast('运行成功 · ' + (data.order || []).join(' → ') + ' · 总耗时 ' + data.total_ms + ' ms', 'ok');
        setStatus('✓ 运行成功 · run_id ' + data.run_id + ' · ' + data.total_ms + ' ms', '右侧「输出变量树」「执行摘要」已刷新');
      } else {
        toast(data.error || '运行未完成，请查看执行摘要', 'err');
        setStatus('✕ ' + (data.error || '运行未完成'), '请查看执行摘要');
      }
      if (!S.selected && S.nodes.length) S.selected = S.nodes[0].node_id;
      setTab('outputs');
      if (!data.ok) setTab('summary');
    } catch (err) {
      toast('请求失败：' + err.message, 'err');
      setStatus('请求失败：' + err.message);
    } finally {
      S.running = false;
      $('btnRun').disabled = false;
      $('btnRun').innerHTML = '<span class="play">▶</span> 运行数据流';
      document.querySelectorAll('#nodesLayer .node').forEach((el) => el.classList.remove('running'));
    }
  }

  // ===================================================================== //
  // 交互绑定
  // ===================================================================== //
  function bindCanvas() {
    const canvas = $('canvas');
    canvas.addEventListener('dragover', (e) => { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; });
    canvas.addEventListener('drop', (e) => {
      e.preventDefault();
      const mid = e.dataTransfer.getData('text/plain');
      if (!mid || !S.byId[mid]) return;
      const base = $('canvasInner').getBoundingClientRect();
      addNode(mid, clamp(Math.round(e.clientX - base.left - 100), 10, 1500),
        clamp(Math.round(e.clientY - base.top - 24), 10, 1100));
    });

    // 选中 / 双击
    $('nodesLayer').addEventListener('click', (e) => {
      const el = e.target.closest('.node');
      if (!el || S.link) return;
      S.selected = el.dataset.id;
      renderNodes(); renderInspector();
    });
    $('nodesLayer').addEventListener('dblclick', (e) => {
      const el = e.target.closest('.node');
      if (!el) return;
      S.selected = el.dataset.id;
      const n = getNode(S.selected);
      if (n && n.is_placeholder) { openScriptEditor(n); return; }
      setTab('params');
    });

    // 连线：从输出端口开始（点击式 + 拖拽式共用一套 pointer 逻辑）
    document.addEventListener('pointerdown', (e) => {
      const shape = e.target.closest('.port-row .shape');
      const head = e.target.closest('.node-head');
      if (head && !shape) {
        const el = head.closest('.node');
        const n = getNode(el.dataset.id);
        if (n) S.drag = { id: n.node_id, sx: e.clientX, sy: e.clientY, ox: n.x, oy: n.y };
        return;
      }
      if (!shape) return;
      const row = shape.closest('.port-row');
      if (row.dataset.side !== 'out') return;
      const nodeEl = row.closest('.node');
      e.preventDefault();
      S.link = { from: nodeEl.dataset.id, from_port: row.dataset.port, sx: e.clientX, sy: e.clientY, moved: false };
      document.querySelectorAll('.port-col.in .port-row').forEach((r) => r.classList.add('link-target'));
    }, true);

    document.addEventListener('pointermove', (e) => {
      if (S.drag) {
        const n = getNode(S.drag.id);
        if (!n) return;
        n.x = clamp(Math.round(S.drag.ox + e.clientX - S.drag.sx), 4, 1560);
        n.y = clamp(Math.round(S.drag.oy + e.clientY - S.drag.sy), 4, 1120);
        const el = document.querySelector('#nodesLayer .node[data-id="' + n.node_id + '"]');
        if (el) { el.style.left = n.x + 'px'; el.style.top = n.y + 'px'; }
        renderWires();
      }
      if (S.link) {
        if (Math.hypot(e.clientX - S.link.sx, e.clientY - S.link.sy) > 6) S.link.moved = true;
        const a = portCenter(S.link.from, 'out', S.link.from_port);
        if (!a) return;
        const base = $('canvasInner').getBoundingClientRect();
        const b = { x: e.clientX - base.left, y: e.clientY - base.top };
        const draft = $('draftWire');
        draft.setAttribute('d', bezier(a, b));
        draft.hidden = false;
      }
    });

    document.addEventListener('pointerup', (e) => {
      S.drag = null;
      if (!S.link) return;
      const hit = document.elementFromPoint(e.clientX, e.clientY);
      const shape = hit && hit.closest('.port-row .shape');
      const row = shape && shape.closest('.port-row');
      const nodeEl = row && row.closest('.node');
      if (row && row.dataset.side === 'in' && nodeEl && nodeEl.dataset.id !== S.link.from) {
        const link = S.link;
        clearLink();
        tryConnect(link.from, link.from_port, nodeEl.dataset.id, row.dataset.port);
      } else if (S.link.moved) {
        clearLink();
      } else {
        $('draftWire').hidden = true;
        setStatus('已选中输出端口，请点击目标输入端口完成连线（Esc 取消）');
      }
    });

    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        if (S.link) { clearLink(); toast('已取消连线'); }
        closeModal('confirmModal'); closeModal('scriptModal');
      }
    });

    $('badgeLayer').addEventListener('click', (e) => {
      const badge = e.target.closest('.edge-badge');
      if (!badge) return;
      const edge = getEdge(badge.dataset.edge);
      if (!edge) return;
      insertAdapter(edge);
      revalidateEdges(); renderAll();
      toast('已在连线上插入适配器占位节点，双击它可编辑转换脚本', 'ok');
    });

    $('canvas').addEventListener('scroll', () => { /* 坐标相对 canvasInner，滚动无需重算 */ });
  }

  function clearLink() {
    S.link = null;
    $('draftWire').hidden = true;
    $('draftWire').setAttribute('d', '');
    document.querySelectorAll('.link-target').forEach((r) => r.classList.remove('link-target'));
    renderNodes();
  }

  function bindInspector() {
    $('tabs').addEventListener('click', (e) => {
      const b = e.target.closest('.tab');
      if (b) setTab(b.dataset.tab);
    });

    $('tabBody').addEventListener('click', (e) => {
      const row = e.target.closest('[data-tree-toggle]');
      if (row && !row.classList.contains('leaf')) {
        const kids = row.parentElement.querySelector('.tree-children');
        if (kids) {
          kids.classList.toggle('hidden');
          row.querySelector('.tree-caret').textContent = kids.classList.contains('hidden') ? '▸' : '▾';
        }
      }
      const all = e.target.closest('[data-tree-all]');
      if (all) {
        const open = all.dataset.treeAll === '1';
        $('tabBody').querySelectorAll('.tree-children').forEach((k) => k.classList.toggle('hidden', !open));
        $('tabBody').querySelectorAll('.tree-caret').forEach((c) => { if (c.textContent !== '·') c.textContent = open ? '▾' : '▸'; });
      }
      if (e.target.closest('#btnDeleteNode')) { removeNode(S.selected); toast('已删除节点'); }
      if (e.target.closest('#btnEditScript')) { const n = getNode(S.selected); if (n) openScriptEditor(n); }
      const act = e.target.closest('[data-act]');
      if (act) {
        const node = getNode(S.selected);
        if (!node) return;
        if (act.dataset.act === 'editScript') openScriptEditor(node);
        if (act.dataset.act === 'newScript') openNewScript(node);
        if (act.dataset.act === 'refreshScripts') refreshScripts();
        if (act.dataset.act === 'addEnv') addCustomEnv();
      }
    });

    $('tabBody').addEventListener('input', (e) => {
      const t = e.target;
      const n = getNode(S.selected);
      if (!n) return;
      if (t.dataset.param) {
        const spec = ((n._manifest && n._manifest.params) || []).find((p) => p.key === t.dataset.param);
        let v = t.type === 'checkbox' ? t.checked : t.value;
        if (spec && spec.type === 'int') v = parseInt(v, 10) || 0;
        n.params[t.dataset.param] = v;
        if (isViewNode(n)) requestAnimationFrame(repaintViews);
      }
      if (t.hasAttribute('data-entry')) n.entry = t.value;
      if (t.dataset.fmt === 'out') {
        const idx = parseInt(t.dataset.index, 10);
        if (n.outputs[idx]) n.outputs[idx].format_hint = t.value;
        revalidateEdges(); renderWires(); renderNodes(); updateStats();
      }
    });
    $('tabBody').addEventListener('change', (e) => {
      const t = e.target;
      const n = getNode(S.selected);
      if (n && t.hasAttribute('data-env')) {
        n.env_id = t.value;
        const env = S.envs.find((x) => x.id === n.env_id);
        renderInspector(); renderNodes();
        toast('环境已切换为 ' + (env ? env.label : '内置') + (env && env.kind === 'external' ? '（子进程 JSON 桥接）' : ''), 'ok');
      }
      if (n && t.hasAttribute('data-script') && t.value !== '__inline__') {
        const item = S.scripts.find((s) => s.path === t.value);
        const names = usableFunctions(item).map((f) => f.name);
        n.entry = t.value;
        n.script = null;
        n.entry_fn = (item && item.default_fn) || (names.includes(n.entry_fn) ? n.entry_fn : names[0]) || 'execute';
        renderInspector(); renderNodes();
        toast('已切换脚本：' + t.value + ' · 入口 ' + n.entry_fn + '()', 'ok');
      }
      if (n && t.hasAttribute('data-fn')) {
        n.entry_fn = t.value;
        renderInspector(); renderNodes();
      }
      if (e.target.dataset.fmt === 'out') {
        renderInspector();
        const warn = S.edges.filter((x) => x.incompatible && !x.via_adapter).length;
        if (warn) toast('检测到 ' + warn + ' 条不兼容连线，画布上已出现「适配器占位」提示徽章', 'warn');
      }
      if (n && t.hasAttribute('data-source-mode')) {
        n.source_mode = t.value;
        renderInspector();
        toast('数据来源已切换为 ' + (t.value === 'js' ? '浏览器 JS · 本地生成器' : '后端 Python · SSE 真实执行'));
        restartStream('数据来源已切换');
      }
      if (n && t.hasAttribute('data-js-gen')) {
        n.js_generator = t.value;
        renderInspector();
        toast('JS 生成器已切换为 ' + t.value);
        restartStream('JS 生成器已切换');
      }
      if (n && t.dataset.param && isViewNode(n)) requestAnimationFrame(repaintViews);
    });
  }

  async function openScriptEditor(n, mode) {
    S.scriptTarget = n.node_id;
    const currentPath = n.entry || '';
    let text = n.script || '';
    if (!text && currentPath) {
      try {
        const d = await fetch('/api/script?path=' + encodeURIComponent(currentPath)).then((r) => r.json());
        text = d.script || '';
      } catch (err) { text = ''; }
    }
    if (mode === 'new') text = NEW_SCRIPT_TEMPLATE;
    if (!text) text = n.is_placeholder ? DEFAULT_PLACEHOLDER_SCRIPT : NEW_SCRIPT_TEMPLATE;
    const path = mode === 'new' ? ('scripts/user_' + n.node_id + '.py')
      : (currentPath || 'scripts/' + n.node_id + '.py');
    $('scriptEditor').value = text;
    $('scriptPath').value = path;
    $('scriptModalTitle').textContent = (mode === 'new' ? '新建脚本' : '编辑脚本') +
      ' · ' + n.name + '（' + n.node_id + '）';
    $('scriptStatus').textContent = currentPath && mode !== 'new'
      ? '当前文件：' + currentPath + ' · 保存后立即写盘'
      : '新脚本 · 保存后写入 scripts/，随后可在「脚本文件」下拉里选中';
    openModal('scriptModal');
  }

  function openNewScript(n) { openScriptEditor(n, 'new'); }

  async function refreshScripts(notify) {
    try {
      const d = await fetch('/api/scripts').then((r) => r.json());
      S.scripts = d.scripts || [];
      renderInspector();
      if (notify !== false) toast('脚本列表已刷新 · ' + S.scripts.length + ' 个文件', 'ok');
    } catch (err) { toast('刷新脚本列表失败：' + err.message, 'err'); }
  }

  async function addCustomEnv() {
    const input = $('customEnvPath');
    const path = input ? input.value.trim() : '';
    if (!path) { toast('请先填写 Python 解释器的绝对路径', 'warn'); return; }
    try {
      const res = await fetch('/api/envs', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path }),
      });
      const d = await res.json();
      if (!res.ok || d.error) throw new Error(d.error || ('HTTP ' + res.status));
      S.envs = d.envs || S.envs;
      const n = getNode(S.selected);
      if (n) n.env_id = d.env.id;
      renderInspector(); renderNodes();
      toast('已登记并选中：' + d.env.label, 'ok');
    } catch (err) { toast('登记环境失败：' + err.message, 'err'); }
  }

  async function saveScript() {
    const n = getNode(S.scriptTarget);
    if (!n) return;
    const text = $('scriptEditor').value;
    const path = $('scriptPath').value.trim() || ('scripts/' + n.node_id + '.py');
    try {
      const res = await fetch('/api/script', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path, script: text }),
      });
      const d = await res.json();
      if (!res.ok || d.error) throw new Error(d.error || ('HTTP ' + res.status));
      n.entry = d.path;
      n.script = null;                 // 保存后以文件为准，不再用内联覆盖
      await refreshScripts(false);
      const item = scriptItem(n);
      const names = usableFunctions(item).map((f) => f.name);
      if (!names.includes(n.entry_fn)) n.entry_fn = (item && item.default_fn) || names[0] || 'execute';
      $('scriptStatus').textContent = '✓ 已写回 ' + d.path + '（' + d.bytes + ' 字节）· 发现 ' +
        names.length + ' 个顶层函数，可在「入口函数」下拉里选择';
      toast('脚本已保存：' + d.path, 'ok');
      renderInspector(); renderNodes();
    } catch (err) {
      $('scriptStatus').textContent = '保存失败：' + err.message;
    }
  }

  function bindTopbar() {
    $('btnRun').addEventListener('click', runFlow);
    $('btnReset').addEventListener('click', async () => {
      if (S.stream.active) await stopStream(true);
      S.nodes = []; S.edges = []; S.results = {}; S.lastRun = null; S.selected = null;
      S.viewState = {};
      S.seqN = 0; S.seqE = 0; S.seqP = 0;
      revalidateEdges(); renderAll();
      toast('画布已清空');
    });
    $('btnExample').addEventListener('click', async () => {
      if (S.stream.active) await stopStream(true);
      loadExample();
    });
    $('btnSeries').addEventListener('click', async () => {
      if (S.stream.active) await stopStream(true);
      loadSeriesExample();
    });
    $('btnStreamDemo').addEventListener('click', () => loadStreamExample('both'));
    $('btnCandleExample').addEventListener('click', () => loadStreamExample('candle'));
    $('btnSignalExample').addEventListener('click', () => loadStreamExample('signal'));
    $('btnStream').addEventListener('click', toggleStream);
    $('streamInterval').addEventListener('change', (e) => {
      const v = parseInt(e.target.value, 10) || 500;
      if (S.stream.active) restartStream('推流间隔改为 ' + v + ' ms');
      else { S.stream.interval = v; toast('推流间隔设为 ' + v + ' ms'); }
    });
    $('btnFit').addEventListener('click', () => { $('canvas').scrollTo({ left: 0, top: 0, behavior: 'smooth' }); });
    $('libSearch').addEventListener('input', (e) => { S.filter = e.target.value; renderLibrary(); });

    // 页面关闭 / 刷新时尽力通知后端收掉 tick 线程；后端另有 20s 闲置兜底
    window.addEventListener('beforeunload', () => {
      if (!S.stream.active || !navigator.sendBeacon) return;
      try {
        navigator.sendBeacon('/api/stream/stop',
          new Blob([JSON.stringify({ run_id: S.stream.runId })], { type: 'application/json' }));
      } catch (err) { /* 卸载阶段忽略 */ }
    });

    document.querySelectorAll('[data-close]').forEach((b) => b.addEventListener('click', () => closeModal(b.dataset.close)));
    $('confirmFoot').addEventListener('click', (e) => {
      const b = e.target.closest('[data-act]');
      if (!b) return;
      closeModal('confirmModal');
      const cb = S.pendingConfirm; S.pendingConfirm = null;
      if (cb) cb(b.dataset.act);
    });
    $('btnSaveScript').addEventListener('click', saveScript);
  }

  function loadExample() {
    if (!S.byId.py_source || !S.byId.py_adapter || !S.byId.shell_consumer) {
      toast('模块库未加载，请确认后端已启动', 'err'); return;
    }
    S.nodes = []; S.edges = []; S.results = {}; S.lastRun = null;
    S.seqN = 0; S.seqE = 0; S.seqP = 0;
    const a = mkNode('py_source', 40, 150);
    const b = mkNode('py_adapter', 320, 150);
    const c = mkNode('shell_consumer', 600, 150);
    S.nodes = [a, b, c];
    S.edges = [
      { id: uid('e'), from: a.node_id, from_port: 'data', to: b.node_id, to_port: 'data', incompatible: false },
      { id: uid('e'), from: b.node_id, from_port: 'payload', to: c.node_id, to_port: 'payload', incompatible: false },
    ];
    S.selected = a.node_id;
    S.tab = 'params';
    document.querySelectorAll('#tabs .tab').forEach((x) => x.classList.toggle('active', x.dataset.tab === 'params'));
    revalidateEdges(); renderAll();
    $('canvas').scrollTo({ left: 0, top: 0 });
    toast('已加载验收闭环：Python 数据源 → Python 适配器 → Shell 消费者，点击「运行数据流」', 'ok');
    setStatus('验收示例已就绪 · 3 个节点 / 2 条边 · 待运行');
  }

  function loadSeriesExample() {
    if (!S.byId.series_source || !S.byId.series_transform || !S.byId.series_report) {
      toast('随机序列模块未加载，请确认后端已启动后刷新页面', 'err'); return;
    }
    S.nodes = []; S.edges = []; S.results = {}; S.lastRun = null;
    S.seqN = 0; S.seqE = 0; S.seqP = 0;
    const a = mkNode('series_source', 40, 150);
    const b = mkNode('series_transform', 320, 150);
    const c = mkNode('series_report', 600, 150);
    const builtinVersion = S.envs.length ? S.envs[0].version : '';
    const ext = S.envs.find((e) => e.kind === 'external' && e.version !== builtinVersion) ||
      S.envs.find((e) => e.kind === 'external');
    if (ext) b.env_id = ext.id;   // 中间节点换一个不同版本的 Python，演示逐节点选择
    S.nodes = [a, b, c];
    S.edges = [
      { id: uid('e'), from: a.node_id, from_port: 'series', to: b.node_id, to_port: 'seq', incompatible: false },
      { id: uid('e'), from: b.node_id, from_port: 'series', to: c.node_id, to_port: 'series', incompatible: false },
    ];
    S.selected = a.node_id;
    S.tab = 'params';
    document.querySelectorAll('#tabs .tab').forEach((x) => x.classList.toggle('active', x.dataset.tab === 'params'));
    revalidateEdges(); renderAll();
    $('canvas').scrollTo({ left: 0, top: 0 });
    toast('已加载随机序列示例' + (ext ? '；中间节点已切到 ' + ext.version + ' 环境' : '') +
      '，点任一节点可改脚本 / 入口函数 / 环境', 'ok');
    setStatus('随机序列示例就绪 · 3 节点 / 2 边 · 数据源脚本内含多个可任选的函数');
  }

  // ===================================================================== //
  // 启动
  // ===================================================================== //
  async function boot() {
    bindCanvas(); bindInspector(); bindTopbar();
    renderAll();
    try {
      const health = await fetch('/api/health').then((r) => r.json());
      S.online = true;
      $('healthBadge').className = 'health online';
      $('healthBadge').innerHTML = '<i></i>后端在线 · 真实执行';
      $('healthBadge').title = health.python + ' · ' + health.engine + ' · ' + health.module_count + ' 个 Manifest';
      const d = await fetch('/api/modules').then((r) => r.json());
      S.manifests = d.modules || [];
      S.categories = d.categories || [];
      S.byId = Object.fromEntries(S.manifests.map((m) => [m.id, m]));
      const [envData, scriptData] = await Promise.all([
        fetch('/api/envs').then((r) => r.json()),
        fetch('/api/scripts').then((r) => r.json()),
      ]);
      S.envs = envData.envs || [];
      S.scripts = scriptData.scripts || [];
      renderLibrary();
      setStatus('已连接后端 · ' + S.manifests.length + ' 个 Manifest · ' +
        S.scripts.length + ' 个脚本 · ' + S.envs.length + ' 个 Python 环境');
      updateStreamStatus();
      await loadStreamExample('both');   // 打开即见 K 线 + 信号灯双实时链路
    } catch (err) {
      S.online = false;
      $('healthBadge').className = 'health offline';
      $('healthBadge').innerHTML = '<i></i>后端离线';
      $('btnRun').disabled = true;
      $('libList').innerHTML = '<div class="tree-empty">后端未启动。<br>请在项目目录执行<br><code>python server.py</code><br>然后刷新页面。</div>';
      setStatus('后端离线 · 请执行 python server.py 后刷新');
      toast('后端离线：请在 dataflow-demo/ 目录执行 python server.py', 'err');
    }
  }

  boot();
})();
