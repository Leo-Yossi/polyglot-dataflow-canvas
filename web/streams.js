/* streams.js — 浏览器端 JS 实时数据源（实时流的第二通道）
 *
 * 与后端 Python 流通道并列：节点「数据来源」切成 js 后，帧完全在浏览器里产生，
 * 不经过 SSE。两种通道产出完全相同结构的帧，渲染节点无需区分。
 *
 * 想改 JS 源逻辑，直接改下面的 create() 即可；新增一个 key 并写进 REG，
 * 节点面板的「JS 生成器」下拉会自动出现它。
 */
(function () {
  'use strict';

  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const r2 = (v) => Math.round(v * 100) / 100;

  // --------------------------------------------------------------------- //
  // 注册表中每个生成器：create() 返回带 next(frame, params) 的对象
  // --------------------------------------------------------------------- //
  const REG = {
    market: {
      label: 'market · 模拟行情 K 线',
      description: '随机游走生成 OHLC 蜡烛 + MA5/MA10 + 成交量，纯浏览器计算',
      create() {
        let candles = [];
        let price = 100;
        let seed = 7;
        return {
          next(frame, params) {
            const cfg = params || {};
            const window = clamp(parseInt(cfg.window, 10) || 36, 8, 120);
            const vol = clamp(parseFloat(cfg.volatility) || 2, 0.1, 30);
            const start = parseFloat(cfg.start_price) || 100;
            seed = parseInt(cfg.seed, 10) || 7;
            if (!candles.length) {
              price = start;
              const rnd = mulberry(seed);
              for (let i = 0; i < Math.min(14, window); i++) {
                const o = price;
                const c = Math.max(1, o + gauss(rnd) * vol * 0.7 + 0.05);
                candles.push({
                  t: i - Math.min(14, window), open: r2(o),
                  high: r2(Math.max(o, c) + rnd() * vol * 0.6),
                  low: r2(Math.min(o, c) - rnd() * vol * 0.6),
                  close: r2(c), volume: 700 + Math.floor(rnd() * 1900),
                });
                price = c;
              }
            }
            const rnd = mulberry(seed * 100003 + frame);
            const o = price;
            const c = Math.max(1, o + (gauss(rnd) * vol) + (rnd() - 0.5) * 0.7);
            const hi = Math.max(o, c) + rnd() * vol * 0.9;
            const lo = Math.min(o, c) - rnd() * vol * 0.9;
            let volume = 600 + Math.floor(rnd() * 1800);
            if (Math.abs(c - o) > vol * 1.1) volume = Math.floor(volume * 1.9);
            candles.push({
              t: frame, open: r2(o), high: r2(hi), low: r2(lo), close: r2(c), volume,
            });
            if (candles.length > window) candles = candles.slice(-window);
            price = c;
            const firstOpen = candles[0].open || 1;
            return {
              candles: candles,
              last: candles[candles.length - 1],
              price: r2(c),
              change_pct: r2((c - firstOpen) / firstOpen * 100),
              volume,
              frame,
              symbol: 'JS·DEMO600519',
              ma5: ma(candles, 5),
              ma10: ma(candles, 10),
              ma_short: 5, ma_long: 10,
              transform: 'js.market',
            };
          },
        };
      },
    },

    signals: {
      label: 'signals · 电路板信号灯',
      description: '红黄绿相位状态机 + 8 路 LED 的亮 / 灭 / 闪烁 / 故障',
      create() {
        const NAMES = ['PWR', 'CLK', 'TX', 'RX', 'BUS', 'IO1', 'IO2', 'ALM'];
        return {
          next(frame, params) {
            const cfg = params || {};
            const lanes = clamp(parseInt(cfg.lanes, 10) || 8, 1, 12);
            const period = clamp(parseInt(cfg.period, 10) || 12, 6, 60);
            const pct = parseFloat(cfg.fault_rate);
            const faultRate = clamp((isNaN(pct) ? 6 : pct) / 100, 0, 0.5);
            const rnd = mulberry((parseInt(cfg.seed, 10) || 11) * 7919 + frame);
            const phase = frame % period;
            const greenEnd = Math.max(2, Math.floor(period * 0.42));
            const yellowEnd = greenEnd + Math.max(1, Math.floor(period * 0.17));
            const light = phase < greenEnd ? 'green' : (phase < yellowEnd ? 'yellow' : 'red');
            const signals = [];
            let faults = 0;
            for (let i = 0; i < lanes; i++) {
              const name = NAMES[i] || ('IO' + i);
              let st;
              if (name === 'CLK') st = 'blink';
              else if (name === 'PWR') st = 'on';
              else if (name === 'BUS') st = light === 'green' ? 'on' : 'off';
              else if (name === 'ALM') st = light === 'red' ? 'blink' : 'off';
              else st = (frame + i) % 3 === 0 || rnd() > 0.55 ? 'on' : 'off';
              if (rnd() < faultRate) { st = 'fault'; faults++; }
              signals.push({
                id: i, name, state: st,
                voltage: st === 'on' ? 3.3 : (st === 'blink' ? 1.65 : 0),
                value: st === 'on' || st === 'blink',
                since: frame,
              });
            }
            return {
              light,
              lights: { red: light === 'red', yellow: light === 'yellow', green: light === 'green' },
              phase, period, cycle: Math.floor(frame / period), remaining: period - phase,
              signals, faults, frame,
              board_id: 'JS·CTRL-BOARD / REV.A',
            };
          },
        };
      },
    },
  };

  function mulberry(a) {
    let s = a >>> 0;
    return function () {
      s |= 0; s = (s + 0x6D2B79F5) | 0;
      let t = Math.imul(s ^ (s >>> 15), 1 | s);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function gauss(rnd) {
    return Math.sqrt(-2 * Math.log(Math.max(rnd(), 1e-9))) * Math.cos(2 * Math.PI * rnd());
  }

  function ma(bars, n) {
    const out = [];
    let sum = 0;
    for (let i = 0; i < bars.length; i++) {
      sum += bars[i].close;
      if (i >= n) sum -= bars[i - n].close;
      out.push(i + 1 < n ? null : r2(sum / n));
    }
    return out;
  }

  // --------------------------------------------------------------------- //
  // 变量树：让 JS 通道也能在右侧面板看到调试器式类型标注
  // --------------------------------------------------------------------- //
  function typeLabel(v) {
    if (v === null || v === undefined) return 'builtins.NoneType';
    if (Array.isArray(v)) return 'builtins.list';
    if (typeof v === 'object') return 'builtins.dict';
    if (typeof v === 'number') return Number.isInteger(v) ? 'builtins.int' : 'builtins.float';
    if (typeof v === 'boolean') return 'builtins.bool';
    return 'builtins.str';
  }

  function preview(v) {
    if (v === null || v === undefined) return 'None';
    if (typeof v === 'string') return JSON.stringify(v.length > 80 ? v.slice(0, 80) + '…' : v);
    if (Array.isArray(v)) return '[' + v.length + ' items]';
    if (typeof v === 'object') return '{' + Object.keys(v).slice(0, 6).join(', ') + '}';
    return String(v);
  }

  function jsNode(name, v, depth) {
    const node = {
      name: String(name), type_label: typeLabel(v), value_preview: preview(v),
      children: [], kind: '',
    };
    if (depth >= 3) return node;
    if (Array.isArray(v)) {
      node.children = v.slice(0, 20).map((x, i) => jsNode('[' + i + ']', x, depth + 1));
    } else if (v && typeof v === 'object') {
      node.children = Object.keys(v).slice(0, 20).map((k) => jsNode(k, v[k], depth + 1));
    }
    return node;
  }

  function treeOf(data) {
    if (!data || typeof data !== 'object') return [];
    return Object.keys(data).map((k) => jsNode(k, data[k], 0));
  }

  // --------------------------------------------------------------------- //
  // 帧构造：与后端 SSE 帧同构
  // --------------------------------------------------------------------- //
  function makeFrame(sourceNode, data, frameNo, treeIds, withTree, portsById) {
    const port = (sourceNode.outputs && sourceNode.outputs[0] && sourceNode.outputs[0].name) || 'out';
    const ids = treeIds || [sourceNode.node_id];
    const ports = portsById || {};
    const byNode = {};
    // JS 通道里没有后端 transform，因此把同一包数据挂到整条可达链路的
    // 每个节点 / 每个输出端口上，渲染节点无论连在源还是连在变换后都能取到。
    ids.forEach((id) => {
      byNode[id] = {};
      const names = (ports[id] || []).concat([port, 'out']);
      names.forEach((p) => { if (p) byNode[id][p] = data; });
    });
    const tree = {};
    if (withTree) {
      const snap = { inputs: [], outputs: treeOf(data), locals: [] };
      ids.forEach((id) => { tree[id] = snap; });
    }
    return {
      run_id: 'js',
      frame: frameNo,
      ts: Date.now() / 1000,
      source: 'js',
      root: sourceNode.node_id,
      by_node: byNode,
      payload: data,
      tree,
      meta: {
        interval_ms: 0, node_count: 1, nodes: [sourceNode.node_id],
        errors: [], stdout: {}, avg_exec_ms: 0,
        js_generator: sourceNode.js_generator || '',
      },
    };
  }

  window.StreamSources = {
    list() {
      return Object.keys(REG).map((k) => ({ key: k, label: REG[k].label, description: REG[k].description }));
    },
    has(key) { return Object.prototype.hasOwnProperty.call(REG, key); },
    create(key) { return (REG[key] || REG.market).create(); },
    makeFrame,
    treeOf,
  };
})();
