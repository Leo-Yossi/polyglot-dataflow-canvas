/* renderers.js — 画布节点内嵌的实时渲染器（零依赖 SVG / Canvas 手绘）
 *
 * Renderers.ensure(container, node)  建一次 DOM 骨架
 * Renderers.draw(container, node, data, frameInfo)  每帧增量更新，不重建节点
 *
 * 配色遵循 A 股习惯：涨 → 红，跌 → 绿。整体界面是明亮浅色，
 * 只有 PCB 灯板用深墨绿板做局部对比，模拟真实电路板的发光信号灯。
 */
(function () {
  'use strict';

  const UP = '#dc4b40';      // 涨 · 红
  const DOWN = '#16a272';    // 跌 · 绿
  const GRID = '#e8edf5';
  const AXIS = '#8b9ab0';
  const MA5 = '#2f6bff';
  const MA10 = '#df8a2c';

  const LED_NAMES = ['PWR', 'CLK', 'TX', 'RX', 'BUS', 'IO1', 'IO2', 'ALM'];

  // --------------------------------------------------------------------- //
  // 公共
  // --------------------------------------------------------------------- //
  function ensure(container, node) {
    if (container.__viewReady) return;
    container.innerHTML = node.render_type === 'candlestick' ? candleSkeleton() : boardSkeleton();
    container.__viewReady = true;
  }

  function candleSkeleton() {
    return '<div class="view-head">' +
      '<span class="view-symbol">—</span>' +
      '<span class="view-price">—</span>' +
      '<span class="view-chg">—</span></div>' +
      '<canvas class="view-canvas"></canvas>' +
      '<div class="view-foot"><span class="view-hint">等待第一帧…</span>' +
      '<span class="view-frame">frame 0</span></div>';
  }

  function boardSkeleton() {
    const traces =
      '<svg class="pcb-traces" viewBox="0 0 320 132" preserveAspectRatio="none" aria-hidden="true">' +
      '<g fill="none" stroke="#c8a64b" stroke-width="1" opacity=".55">' +
      '<path d="M4 18 H96 V34 H150"/><path d="M4 44 H64 V70 H108"/>' +
      '<path d="M4 92 H120 V112 H210"/><path d="M4 120 H84 V96 H150"/>' +
      '<path d="M170 12 V52 H312"/><path d="M186 126 V88 H312"/>' +
      '</g><g fill="#d8bb6a" opacity=".7">' +
      '<circle cx="96" cy="18" r="2"/><circle cx="64" cy="44" r="2"/>' +
      '<circle cx="120" cy="92" r="2"/><circle cx="170" cy="12" r="2"/>' +
      '<circle cx="186" cy="126" r="2"/></g></svg>';
    return '<div class="pcb">' +
      '<div class="pcb-head"><span class="pcb-title">CTRL-BOARD / REV.A</span>' +
      '<span class="pcb-badge">LIVE</span></div>' +
      '<div class="pcb-main">' + traces +
      '<div class="traffic"><div class="tl red" data-light="red"></div>' +
      '<div class="tl yellow" data-light="yellow"></div>' +
      '<div class="tl green" data-light="green"></div></div>' +
      '<div class="led-grid"></div></div>' +
      '<div class="pcb-foot"><span class="pcb-phase">相位 —</span>' +
      '<span class="pcb-fault">FAULT 0</span>' +
      '<span class="pcb-frame">frame 0</span></div></div>';
  }

  function draw(container, node, data, frameInfo) {
    if (!container) return;
    ensure(container, node);
    if (!data || typeof data !== 'object') return;
    if (node.render_type === 'candlestick') drawCandles(container, data, node.params || {}, frameInfo);
    else drawBoard(container, data, node.params || {}, frameInfo);
  }

  // --------------------------------------------------------------------- //
  // K 线：蜡烛 + MA5 / MA10 + 成交量 + 价格轴
  // --------------------------------------------------------------------- //
  function drawCandles(container, data, params, info) {
    const bars = Array.isArray(data.candles) ? data.candles : [];
    const canvas = container.querySelector('.view-canvas');
    if (!canvas) return;
    const head = container.querySelector('.view-head');
    const price = Number(data.price != null ? data.price : (bars.length ? bars[bars.length - 1].close : 0));
    const chg = Number(data.change_pct || 0);
    const color = chg >= 0 ? UP : DOWN;
    head.querySelector('.view-symbol').textContent = data.symbol || 'DEMO';
    head.querySelector('.view-price').textContent = price.toFixed(2);
    head.querySelector('.view-price').style.color = color;
    const chgEl = head.querySelector('.view-chg');
    chgEl.textContent = (chg >= 0 ? '+' : '') + chg.toFixed(2) + '%';
    chgEl.style.color = color;
    container.querySelector('.view-frame').textContent =
      'frame ' + (info.frame || 0) + ' · ' + (info.source || '-') + ' · ' + (info.fps || 0).toFixed(1) + ' fps';
    container.querySelector('.view-hint').textContent =
      'MA5 ' + lastValue(data.ma5) + '   MA10 ' + lastValue(data.ma10) +
      '   VOL ' + (data.volume != null ? data.volume : '-');

    const W = canvas.clientWidth || 320;
    const H = canvas.clientHeight || 178;
    const dpr = window.devicePixelRatio || 1;
    const pw = Math.round(W * dpr), ph = Math.round(H * dpr);
    if (canvas.width !== pw || canvas.height !== ph) { canvas.width = pw; canvas.height = ph; }
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, W, H);

    if (!bars.length) {
      ctx.fillStyle = AXIS; ctx.font = '11px sans-serif'; ctx.textAlign = 'center';
      ctx.fillText('等待第一帧行情数据…', W / 2, H / 2);
      return;
    }

    const showVol = params.show_volume !== false && params.show_volume !== 'false';
    const showMa = params.show_ma !== false && params.show_ma !== 'false';
    const padL = 6, padR = 46, padT = 8, padB = 16;
    const volH = showVol ? Math.min(38, H * 0.22) : 0;
    const gap = showVol ? 8 : 0;
    const plotH = H - padT - padB - volH - gap;
    const plotW = W - padL - padR;

    let lo = Infinity, hi = -Infinity, maxVol = 1;
    bars.forEach((b) => {
      lo = Math.min(lo, Number(b.low)); hi = Math.max(hi, Number(b.high));
      maxVol = Math.max(maxVol, Number(b.volume) || 0);
    });
    const pad = (hi - lo) * 0.08 || 1;
    lo -= pad; hi += pad;
    const y = (v) => padT + (hi - Number(v)) / (hi - lo) * plotH;

    // 网格与价格轴
    ctx.strokeStyle = GRID; ctx.lineWidth = 1;
    ctx.fillStyle = AXIS; ctx.font = '9px ui-monospace, Consolas, monospace';
    ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
    for (let i = 0; i <= 3; i++) {
      const yy = padT + plotH * i / 3;
      ctx.beginPath(); ctx.setLineDash([3, 3]);
      ctx.moveTo(padL, yy + 0.5); ctx.lineTo(W - padR, yy + 0.5); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillText((hi - (hi - lo) * i / 3).toFixed(2), W - padR + 5, yy);
    }
    if (showVol) {
      ctx.strokeStyle = '#eef2f8';
      ctx.beginPath(); ctx.moveTo(padL, H - padB); ctx.lineTo(W - padR, H - padB); ctx.stroke();
    }

    const slot = plotW / bars.length;
    const bodyW = Math.max(2, Math.min(12, slot * 0.64));
    bars.forEach((b, i) => {
      const cx = padL + slot * (i + 0.5);
      const up = Number(b.close) >= Number(b.open);
      const c = up ? UP : DOWN;
      ctx.strokeStyle = c; ctx.fillStyle = c; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(Math.round(cx) + 0.5, y(b.high));
      ctx.lineTo(Math.round(cx) + 0.5, y(b.low)); ctx.stroke();
      const yo = y(b.open), yc = y(b.close);
      const top = Math.min(yo, yc), h = Math.max(1.2, Math.abs(yc - yo));
      ctx.fillRect(cx - bodyW / 2, top, bodyW, h);
      if (showVol) {
        const vh = Math.max(1, (Number(b.volume) || 0) / maxVol * (volH - 4));
        ctx.globalAlpha = 0.62;
        ctx.fillRect(cx - bodyW / 2, H - padB - vh, bodyW, vh);
        ctx.globalAlpha = 1;
      }
    });

    if (showMa) {
      [['ma5', MA5], ['ma10', MA10]].forEach(([key, color]) => {
        let series = data[key];
        if (!Array.isArray(series) || series.length !== bars.length) series = calcMa(bars, key === 'ma5' ? 5 : 10);
        ctx.strokeStyle = color; ctx.lineWidth = 1.5; ctx.beginPath();
        let started = false;
        series.forEach((v, i) => {
          if (v == null) { started = false; return; }
          const px = padL + slot * (i + 0.5), py = y(v);
          if (!started) { ctx.moveTo(px, py); started = true; } else ctx.lineTo(px, py);
        });
        ctx.stroke();
      });
    }

    // 最新价虚线与标签
    const ly = y(price);
    if (ly > padT && ly < padT + plotH) {
      ctx.strokeStyle = color; ctx.globalAlpha = 0.65; ctx.setLineDash([4, 3]);
      ctx.beginPath(); ctx.moveTo(padL, ly); ctx.lineTo(W - padR, ly); ctx.stroke();
      ctx.setLineDash([]); ctx.globalAlpha = 1;
      ctx.fillStyle = color;
      ctx.fillRect(W - padR + 1, ly - 8, padR - 2, 16);
      ctx.fillStyle = '#fff'; ctx.font = 'bold 9px ui-monospace, Consolas, monospace';
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText(price.toFixed(2), W - padR / 2, ly);
    }

    // 时间轴
    ctx.fillStyle = AXIS; ctx.font = '9px ui-monospace, Consolas, monospace';
    ctx.textAlign = 'center'; ctx.textBaseline = 'top';
    const firstT = bars[0].t, lastT = bars[bars.length - 1].t;
    ctx.fillText(String(firstT), padL + slot / 2, H - padB + 3);
    ctx.fillText(String(lastT), padL + plotW - slot / 2, H - padB + 3);
  }

  function calcMa(bars, n) {
    const out = []; let sum = 0;
    for (let i = 0; i < bars.length; i++) {
      sum += Number(bars[i].close) || 0;
      if (i >= n) sum -= Number(bars[i - n].close) || 0;
      out.push(i + 1 < n ? null : sum / n);
    }
    return out;
  }

  function lastValue(arr) {
    if (!Array.isArray(arr)) return '—';
    for (let i = arr.length - 1; i >= 0; i--) if (arr[i] != null) return Number(arr[i]).toFixed(2);
    return '—';
  }

  // --------------------------------------------------------------------- //
  // 电路板信号灯：三色交通灯 + 8 路 LED 阵列
  // --------------------------------------------------------------------- //
  function drawBoard(container, data, params, info) {
    const pcb = container.querySelector('.pcb');
    if (!pcb) return;
    const signals = Array.isArray(data.signals) ? data.signals : [];
    const light = data.light || 'green';
    pcb.querySelectorAll('.tl').forEach((el) => {
      el.classList.toggle('on', el.dataset.light === light);
    });
    pcb.querySelector('.pcb-title').textContent = data.board_id || 'CTRL-BOARD / REV.A';
    pcb.querySelector('.pcb-phase').textContent =
      '相位 ' + (data.phase != null ? data.phase : '—') + '/' + (data.period || '—') +
      ' · 周期 ' + (data.cycle != null ? data.cycle : '—');
    const faultEl = pcb.querySelector('.pcb-fault');
    faultEl.textContent = 'FAULT ' + (data.faults || 0);
    faultEl.classList.toggle('has-fault', (data.faults || 0) > 0);
    pcb.querySelector('.pcb-frame').textContent =
      'frame ' + (info.frame || 0) + ' · ' + (info.source || '-') + ' · ' + (info.fps || 0).toFixed(1) + ' fps';
    pcb.querySelector('.pcb-badge').textContent = data.faults ? 'ALERT' : 'LIVE';
    pcb.querySelector('.pcb-badge').classList.toggle('alert', !!data.faults);

    const grid = pcb.querySelector('.led-grid');
    const lanes = Math.min(signals.length || 0, Math.max(1, parseInt(params.lanes, 10) || 8));
    if (grid.children.length !== lanes) {
      let html = '';
      for (let i = 0; i < lanes; i++) {
        html += '<div class="led-cell" data-i="' + i + '">' +
          '<span class="led-bulb"></span>' +
          '<span class="led-name">' + (LED_NAMES[i] || ('IO' + i)) + '</span>' +
          '<span class="led-volt">0.0V</span></div>';
      }
      grid.innerHTML = html;
    }
    for (let i = 0; i < lanes; i++) {
      const s = signals[i] || {};
      const cell = grid.children[i];
      const bulb = cell.querySelector('.led-bulb');
      bulb.className = 'led-bulb s-' + (s.state || 'off');
      cell.querySelector('.led-name').textContent = s.name || (LED_NAMES[i] || ('IO' + i));
      cell.querySelector('.led-volt').textContent = Number(s.voltage || 0).toFixed(1) + 'V';
    }
  }

  window.Renderers = { ensure, draw };
})();
