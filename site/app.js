/* ReviewGrowth 复盘页 · 原生 JS + ECharts（无构建步骤）
 * as-of 隔离：当时视角只渲染 available_at <= cursor 的条目，且只读 item.as_of；
 * price_reaction / hindsight 仅事后视角渲染。 */
(function () {
  "use strict";

  const TYPE_COLORS = { E: "#58a6ff", R: "#3fb950", N: "#8b949e", A: "#d29922", C: "#bc8cff" };
  const TYPE_SYMBOL = { E: "circle", R: "diamond", N: "rect", A: "triangle", C: "pin" };
  const TYPE_NAME = { E: "财报", R: "研报", N: "新闻", A: "公告", C: "调研/电话会" };
  const PHASE_COLORS = {
    "潜伏": "#8b949e", "确认": "#58a6ff", "主升": "#f85149",
    "高位震荡": "#d29922", "证伪": "#bc8cff", "修复": "#3fb950", "再平衡": "#79c0ff",
  };
  const E_R_PERSIST_DAYS = 180, OTHER_PERSIST_DAYS = 90;

  const qp = new URLSearchParams(location.search);
  const zoomParam = (qp.get("zoom") || "").split("-").map(Number);
  const S = {
    code: qp.get("code") || "",
    mode: qp.get("mode") === "hindsight" ? "hindsight" : "as_of",
    cursor: qp.get("date") || null,
    selected: qp.get("sel") || null,
    prevCursor: null,
    theme: qp.get("theme") || localStorage.getItem("rg_theme") || "dark",
    zoom: (zoomParam.length === 2 && zoomParam.every((n) => !isNaN(n))) ? { start: zoomParam[0], end: zoomParam[1] } : null,
    filters: { E: 1, R: 1, N: 1, A: 1, C: 1 },
    site: null,
  };

  /* ── 主题调色板（暗/亮） ── */
  function pal() {
    return document.body.classList.contains("light")
      ? { text: "#59636e", line: "#d0d7de", grid: "#e4e8ed", ttBg: "#ffffff", ttBorder: "#d0d7de",
          ttText: "#1f2328", bandLabel: "#59636e", cursorLine: "#0969da", axisBg: "#f0f3f6" }
      : { text: "#8b949e", line: "#30363d", grid: "#21262d", ttBg: "#161b22", ttBorder: "#30363d",
          ttText: "#c9d1d9", bandLabel: "#c9d1d9", cursorLine: "#58a6ff", axisBg: "#1c2128" };
  }

  function applyTheme() {
    document.body.classList.toggle("light", S.theme === "light");
    const btn = $("theme-btn");
    if (btn) btn.textContent = S.theme === "light" ? "暗色" : "亮色";
  }

  /* ── 工具 ── */
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const signCls = (v) => (v == null ? "" : v > 0 ? "up" : v < 0 ? "down" : "");
  const fmtPct = (v, d = 1) => (v == null ? "—" : (v > 0 ? "+" : "") + (v * 100).toFixed(d) + "%");
  const fmtPctRaw = (v, d = 1) => (v == null ? "—" : (v > 0 ? "+" : "") + v.toFixed(d) + "%");
  const fmtYi = (v, d = 1) => (v == null ? "—" : (v / 1e8).toFixed(d) + "亿");
  const fmtNum = (v, d = 2) => (v == null ? "—" : v.toFixed(d));
  const addDays = (ds, n) => { const d = new Date(ds + "T00:00:00"); d.setDate(d.getDate() + n); return d.toISOString().slice(0, 10); };

  let klineChart, consensusChart, valChart;

  /* ── 数据访问 ── */
  const inAsOf = () => S.mode === "as_of";
  const tlAll = () => S.site.timeline;
  const tlVisible = () => {
    const items = S.site.timeline.filter((it) => !inAsOf() || it.available_at <= S.cursor);
    return items.filter((it) => S.filters[it.type]);
  };
  const barsDisplay = () => {
    const start = S.site.meta.start_date;
    let bars = S.site.bars.filter((b) => b.d >= start);
    if (inAsOf()) bars = bars.filter((b) => b.d <= S.cursor);
    return bars;
  };

  /* ── 启动 ── */
  function boot() {
    if (!S.code) { document.body.innerHTML = "<p style='padding:40px'>缺少 ?code= 参数，<a href='index.html'>返回列表</a></p>"; return; }
    fetch("data/" + S.code + "/site.json").then((r) => {
      if (!r.ok) throw new Error("site.json " + r.status);
      return r.json();
    }).then((site) => {
      S.site = site;
      S.cursor = S.cursor || site.meta.end_date;
      if (S.cursor > site.meta.bar_range[1]) S.cursor = site.meta.bar_range[1];
      bindStaticEvents();
      renderAll();
    }).catch((e) => {
      document.body.innerHTML = "<p style='padding:40px'>加载失败: " + esc(e.message) + " · <a href='index.html'>返回列表</a></p>";
    });
  }

  function bindStaticEvents() {
    document.title = S.site.meta.name + " 复盘 · ReviewGrowth";
    applyTheme();
    $("stock-name").textContent = S.site.meta.name + " " + S.site.meta.code;
    $("stock-range").textContent = S.site.meta.start_date + " ~ " + S.site.meta.end_date;
    $("cursor-input").min = S.site.meta.start_date;
    $("cursor-input").max = S.site.meta.bar_range[1];

    document.querySelectorAll("#mode-toggle button").forEach((b) => {
      b.addEventListener("click", () => { S.mode = b.dataset.mode; sync(); renderAll(); });
    });
    $("cursor-input").addEventListener("change", (e) => {
      if (e.target.value) { S.cursor = e.target.value; sync(); renderAll(); ensureFocus(S.cursor); }
    });
    $("cursor-prev").addEventListener("click", () => jumpEvent(-1));
    $("cursor-next").addEventListener("click", () => jumpEvent(1));

    // K线缩放/平移（桌面按钮；移动端手势由 dataZoom inside 提供）
    $("kz-in").addEventListener("click", () => zoomBy(1 / 1.5));
    $("kz-out").addEventListener("click", () => zoomBy(1.5));
    $("kz-left").addEventListener("click", () => panBy(-0.3));
    $("kz-right").addEventListener("click", () => panBy(0.3));
    $("kz-reset").addEventListener("click", () => { S.zoom = null; applyZoom(0, 100); });
    $("asof-banner").addEventListener("click", (e) => {
      if (e.target && e.target.id === "asof-restore") restoreCursor(S.site.meta.end_date, false);
    });

    // 主题切换
    $("theme-btn").addEventListener("click", () => {
      S.theme = S.theme === "light" ? "dark" : "light";
      localStorage.setItem("rg_theme", S.theme);
      applyTheme();
      [klineChart, consensusChart, valChart].forEach((c) => { if (c) c.dispose(); });
      klineChart = consensusChart = valChart = null;
      renderAll();
    });

    // 使用说明
    const closeHelp = () => $("help-modal").classList.add("hidden");
    $("help-btn").addEventListener("click", () => { $("help-modal").classList.remove("hidden"); localStorage.setItem("rg_help_seen", "1"); });
    $("help-close").addEventListener("click", closeHelp);
    $("help-modal").addEventListener("click", (e) => { if (e.target === $("help-modal")) closeHelp(); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeHelp(); });
    if (qp.get("help") !== "0" && (qp.get("help") === "1" || !localStorage.getItem("rg_help_seen"))) {
      $("help-modal").classList.remove("hidden");
      localStorage.setItem("rg_help_seen", "1");
    }

    const chips = $("type-chips");
    Object.keys(TYPE_NAME).forEach((t) => {
      const el = document.createElement("span");
      el.className = "chip on"; el.dataset.t = t;
      el.innerHTML = t + '<span class="ct"> ' + TYPE_NAME[t] + "</span>";
      el.addEventListener("click", () => {
        S.filters[t] = !S.filters[t];
        el.classList.toggle("on", !!S.filters[t]);
        renderTimeline(); renderKline();
      });
      chips.appendChild(el);
    });

    $("report-link").href = "data/" + S.code + "/report.html";
    $("confidence-link").href = "data/" + S.code + "/confidence.html";
    $("data-sources-link").href = "data/data_sources.json";
    window.addEventListener("resize", () => { klineChart && klineChart.resize(); consensusChart && consensusChart.resize(); valChart && valChart.resize(); });
  }

  function jumpEvent(dir) {
    const dates = [...new Set(tlVisible().map((it) => it.available_at))].sort();
    if (!dates.length) return;
    let target = null;
    if (dir > 0) target = dates.find((d) => d > S.cursor) || dates[dates.length - 1];
    else target = [...dates].reverse().find((d) => d < S.cursor) || dates[0];
    moveCursorTo(target);
  }

  function sync() {
    const u = new URLSearchParams();
    u.set("code", S.code);
    if (S.mode !== "as_of") u.set("mode", S.mode);
    u.set("date", S.cursor);
    if (S.selected) u.set("sel", S.selected);
    history.replaceState(null, "", location.pathname + "?" + u.toString());
  }

  /* 选中条目：只做「K线定位 + 右栏详情」，不改变光标（光标=信息可见截止日，须显式移动） */
  function select(id) {
    // 再点同一项 = 取消选择
    if (S.selected === id) { S.selected = null; sync(); renderAll(); return; }
    S.selected = id;
    sync(); renderAll();
    const it = tlAll().find((x) => x.id === id);
    if (it) focusSelected(it.date);
    const el = document.querySelector('.tl-item[data-id="' + CSS.escape(id) + '"]');
    if (el) el.scrollIntoView({ block: "center", behavior: "smooth" });
  }

  /* 把 K 线视图聚焦到某事件（不改光标、不截断）：过于全览时缩放到约一年的友好窗口，否则仅平移 */
  function focusSelected(date) {
    const bars = barsDisplay();
    if (!bars.length || !date) return;
    let idx = bars.findIndex((b) => b.d >= date);
    if (idx < 0) idx = bars.length - 1;
    const total = bars.length;
    const z = currentZoom();
    const curSpan = z.end - z.start;
    const wantSpan = Math.max(8, Math.min(100, (240 / total) * 100));
    const span = Math.min(curSpan, Math.max(wantSpan, 8));
    const sIdx = (z.start / 100) * total, eIdx = (z.end / 100) * total;
    if (curSpan <= span * 1.05 && idx >= sIdx && idx <= eIdx) return;   // 已在舒适窗口内
    let ns = (idx / total) * 100 - span / 2;
    ns = Math.max(0, Math.min(ns, 100 - span));
    applyZoom(ns, ns + span);
  }

  /* 显式移动光标（详情卡按钮/◀▶）：记录上一步以便「返回」 */
  function moveCursorTo(date) {
    if (!date) return;
    const br = (S.site && S.site.meta.bar_range) || [];
    if (br[0] && date < br[0]) date = br[0];
    if (br[1] && date > br[1]) date = br[1];
    if (date === S.cursor) return;
    S.prevCursor = S.cursor;
    S.cursor = date;
    sync(); renderAll();
    ensureFocus(date);
  }

  /* 还原光标 / 清除选择 */
  function restoreCursor(destCursor, clearSel) {
    if (destCursor) S.cursor = destCursor;
    if (clearSel) S.selected = null;
    S.prevCursor = null;
    sync(); renderAll();
    if (destCursor) ensureFocus(destCursor);
  }

  /* 标注自适应：按当前缩放窗口的像素密度决定是否显示 K 线标注（全览干净、放大后出现） */
  function markerLabelsOn(barsCount) {
    const chartW = (klineChart && klineChart.getWidth()) || 800;
    const spanPct = S.zoom ? (S.zoom.end - S.zoom.start) : 100;
    const barsShown = Math.max(15, barsCount * spanPct / 100);
    return 45 * (chartW / barsShown) >= 42;
  }

  function currentZoom() {
    if (!klineChart) return { start: 0, end: 100 };
    const dz = (klineChart.getOption().dataZoom || [])[0] || {};
    return { start: dz.start == null ? 0 : dz.start, end: dz.end == null ? 100 : dz.end };
  }

  function applyZoom(start, end) {
    if (!klineChart) return;
    const span = Math.min(100, Math.max(2, end - start));
    const ns = Math.max(0, Math.min(start, 100 - span));
    klineChart.dispatchAction({ type: "dataZoom", start: ns, end: ns + span });
    S.zoom = (ns <= 0.01 && ns + span >= 99.99) ? null : { start: ns, end: ns + span };
  }

  function zoomBy(factor) {
    const { start, end } = currentZoom();
    const span = Math.min(100, Math.max(2, (end - start) * factor));
    const center = (start + end) / 2;
    applyZoom(center - span / 2, center + span / 2);
  }

  function panBy(frac) {
    const { start, end } = currentZoom();
    const span = end - start;
    applyZoom(start + span * frac, end + span * frac);
  }

  /* 缩放状态下导航（选中/光标跳转）时，确保目标日期在可视窗口内（不在则居中到当前跨度） */
  function ensureFocus(date) {
    const z = currentZoom();
    if (z.start <= 0.01 && z.end >= 99.99) return;
    const bars = barsDisplay();
    if (!bars.length || !date) return;
    let idx = bars.findIndex((b) => b.d >= date);
    if (idx < 0) idx = bars.length - 1;
    const total = bars.length;
    const sIdx = (z.start / 100) * total, eIdx = (z.end / 100) * total;
    if (idx >= sIdx && idx <= eIdx) return;
    const span = z.end - z.start;
    let ns = (idx / total) * 100 - span / 2;
    ns = Math.max(0, Math.min(ns, 100 - span));
    applyZoom(ns, ns + span);
  }

  /* ── 渲染总入口 ── */
  function renderAll() {
    document.querySelectorAll("#mode-toggle button").forEach((b) =>
      b.classList.toggle("active", b.dataset.mode === S.mode));
    $("cursor-input").value = S.cursor;
    $("asof-cursor").textContent = "@ " + S.cursor + (inAsOf() ? "（可见信息截至该日）" : "（事后复盘）");
    renderCursorBanner();
    renderCounts(); renderTimeline(); renderKline(); renderDetail();
    renderInfoPackage(); renderConsensus(); renderValuationChart();
    renderEarningsTable(); renderLogic(); renderReview(); renderFooter(); renderPhaseLegend();
  }

  /* 当时视角的截断提示（显示在图表上方，一键恢复全览） */
  function renderCursorBanner() {
    const banner = $("asof-banner");
    if (!banner) return;
    const truncated = inAsOf() && S.cursor < S.site.meta.end_date;
    banner.classList.toggle("hidden", !truncated);
    if (truncated) {
      const n = tlAll().filter((it) => it.available_at > S.cursor).length;
      banner.innerHTML = "当时视角：已隐藏 " + esc(S.cursor) + " 之后的 " + n + ' 条信息 · <button id="asof-restore">恢复全览</button>';
    }
  }

  function renderCounts() {
    const vis = tlVisible();
    ["high", "medium", "low"].forEach((c) => {
      $("cnt-" + c).textContent = vis.filter((it) => it.confidence === c).length;
    });
  }

  /* ── 时间轴 ── */
  function renderTimeline() {
    const all = tlAll().filter((it) => S.filters[it.type]);
    const vis = all.filter((it) => !inAsOf() || it.available_at <= S.cursor);
    const hidden = all.length - vis.length;
    $("timeline-count").textContent = inAsOf() ? vis.length + " 条可见" : all.length + " 条";
    const rows = [];
    const ordered = [...vis].sort((a, b) => (a.available_at < b.available_at ? 1 : -1));
    ordered.forEach((it) => {
      const sel = it.id === S.selected ? " sel" : "";
      const state = (it.as_of && it.as_of.logic_state) ? '<span class="state ' + esc(it.as_of.logic_state) + '">' + esc(it.as_of.logic_state) + "</span>" : "";
      let react = "";
      if (!inAsOf() && it.price_reaction && it.price_reaction.t20 != null) {
        const v = it.price_reaction.t20;
        react = '<span class="' + signCls(v) + '" style="font-size:.8em">T+20 ' + fmtPctRaw(v) + "</span>";
      }
      rows.push(
        '<div class="tl-item' + sel + '" data-id="' + esc(it.id) + '">' +
        '<span class="tbadge ' + esc(it.type) + '">' + esc(it.type) + "</span>" +
        '<div class="tl-body"><div class="tl-title">' + esc(it.title) + "</div>" +
        '<div class="tl-flags"><span class="tl-date">' + esc(it.available_at) + "</span>" +
        '<span class="dot ' + esc(it.confidence) + '" title="置信度"></span>' + state + react +
        "</div></div></div>");
    });
    if (inAsOf() && hidden > 0) {
      rows.unshift('<div class="tl-sep">cursor 之后不可见 ' + hidden + " 条 ↓</div>");
    }
    $("timeline").innerHTML = rows.join("") || "<p class='sub'>暂无</p>";
    $("timeline").querySelectorAll(".tl-item").forEach((el) =>
      el.addEventListener("click", () => select(el.dataset.id)));
  }

  /* ── K线 ── */
  function maSeries(bars, n) {
    const out = []; let sum = 0;
    for (let i = 0; i < bars.length; i++) {
      sum += bars[i].c;
      if (i >= n) sum -= bars[i - n].c;
      out.push(i >= n - 1 ? +(sum / n).toFixed(2) : null);
    }
    return out;
  }

  function renderKline() {
    if (!klineChart) klineChart = echarts.init($("kline"), null, { renderer: "canvas" });
    const all = S.site.bars;
    const disp = barsDisplay();
    if (!disp.length) { klineChart.clear(); return; }
    const dates = disp.map((b) => b.d);
    const ohlc = disp.map((b) => [b.o, b.c, b.l, b.h]);
    const vols = disp.map((b, i) => ({ value: b.v, itemStyle: { color: b.c >= b.o ? "rgba(248,81,73,.5)" : "rgba(63,185,80,.5)" } }));
    const maAll = { 5: maSeries(all, 5), 10: maSeries(all, 10), 20: maSeries(all, 20), 60: maSeries(all, 60) };
    const idx0 = all.findIndex((b) => b.d === disp[0].d);
    const slice = (arr) => arr.slice(idx0, idx0 + disp.length);

    // 相位色带（名称与收益见图表下方图例；此处仅保留色带，避免在全览/窄屏下标签拥挤）
    const markAreas = [];
    S.site.phases.forEach((ph) => {
      let end = ph.end;
      if (inAsOf() && end > S.cursor) end = S.cursor;
      const startD = ph.start < dates[0] ? dates[0] : ph.start;
      if (end < dates[0] || startD > dates[dates.length - 1]) return;
      markAreas.push([
        { xAxis: startD, itemStyle: { color: hexA(PHASE_COLORS[ph.name] || "#8b949e", 0.12) } },
        { xAxis: end },
      ]);
    });

    // 事件标记（高重要性条目带 kline_label 短标注；按缩放密度自适应 + 密集时上下交错）
    const priceByDate = {};
    disp.forEach((b) => { priceByDate[b.d] = b; });
    const P = pal();
    const marks = [];
    const showMarkerLabels = markerLabelsOn(disp.length);
    S._labelsOn = showMarkerLabels;
    let lastLabelIdx = -999, labelOnTop = true;
    tlVisible().forEach((it) => {
      let d = it.date;
      if (!priceByDate[d]) { const nxt = dates.find((x) => x >= d); if (!nxt) return; d = nxt; }
      const idx = dates.indexOf(d);
      const isSel = it.id === S.selected;
      let label = { show: false };
      if (it.kline_label && showMarkerLabels) {
        labelOnTop = (idx - lastLabelIdx < 45) ? !labelOnTop : true;
        lastLabelIdx = idx;
        label = {
          show: true, formatter: it.kline_label,
          position: labelOnTop ? "top" : "bottom",
          fontSize: 10, color: P.bandLabel, backgroundColor: "transparent",
        };
      }
      marks.push({
        name: it.id, coord: [d, priceByDate[d].h], value: it.type, itemId: it.id,
        symbol: TYPE_SYMBOL[it.type], symbolSize: (it.kline_label ? 16 : 15) + (isSel ? 5 : 0),
        itemStyle: { color: TYPE_COLORS[it.type], borderColor: isSel ? P.cursorLine : P.ttBg, borderWidth: isSel ? 2 : 1 },
        label,
      });
    });

    // 反应窗口指引线（事后视角 + 选中项）
    const markLines = [];
    if (!inAsOf() && S.selected) {
      const it = tlAll().find((x) => x.id === S.selected);
      if (it && it.price_reaction && it.price_reaction.base_date) {
        const bd = it.price_reaction.base_date;
        const baseIdx = all.findIndex((b) => b.d === bd);
        [["基准", 0], ["T+1", 1], ["T+5", 5], ["T+20", 20], ["T+60", 60]].forEach(([label, n]) => {
          const j = baseIdx + n;
          if (j < all.length && all[j].d <= dates[dates.length - 1]) {
            markLines.push([{ xAxis: all[j].d, label: { formatter: label, color: P.text, fontSize: 10 }, lineStyle: { color: P.text, type: "dashed", width: 1 } }, { xAxis: all[j].d }]);
          }
        });
      }
    }
    // as-of 光标线
    if (inAsOf()) {
      markLines.push([{ xAxis: S.cursor, lineStyle: { color: P.cursorLine, width: 1 }, label: { formatter: "cursor " + S.cursor, color: P.cursorLine, fontSize: 10 } }, { xAxis: S.cursor }]);
    }

    klineChart.setOption({
      animation: false,
      backgroundColor: "transparent",
      axisPointer: { link: [{ xAxisIndex: "all" }], label: { backgroundColor: P.axisBg } },
      tooltip: { trigger: "axis", axisPointer: { type: "cross" }, backgroundColor: P.ttBg, borderColor: P.ttBorder, textStyle: { color: P.ttText, fontSize: 12 }, confine: true },
      grid: [
        { left: 56, right: 16, top: 14, height: 300 },
        { left: 56, right: 16, top: 330, height: 70 },
      ],
      xAxis: [
        { type: "category", data: dates, gridIndex: 0, axisLine: { lineStyle: { color: P.line } }, axisLabel: { color: P.text }, axisTick: { show: false }, boundaryGap: true },
        { type: "category", data: dates, gridIndex: 1, axisLabel: { show: false }, axisTick: { show: false }, axisLine: { lineStyle: { color: P.line } } },
      ],
      dataZoom: [
        {
          type: "inside", xAxisIndex: [0, 1],
          start: S.zoom ? S.zoom.start : 0, end: S.zoom ? S.zoom.end : 100,
          minValueSpan: 15, zoomOnMouseWheel: true, moveOnMouseMove: true, moveOnMouseWheel: false,
          throttle: 60,
        },
      ],
      yAxis: [
        { scale: true, gridIndex: 0, splitLine: { lineStyle: { color: P.grid } }, axisLabel: { color: P.text } },
        { gridIndex: 1, splitLine: { show: false }, axisLabel: { show: false } },
      ],
      series: [
        {
          name: "日K", type: "candlestick", data: ohlc, xAxisIndex: 0, yAxisIndex: 0,
          itemStyle: { color: "#f85149", color0: "#3fb950", borderColor: "#f85149", borderColor0: "#3fb950" },
          markArea: {
            silent: true, data: markAreas,
          },
          markPoint: { data: marks },
          markLine: { silent: true, symbol: "none", data: markLines },
        },
        ...[["MA5", 5, "#e3b341"], ["MA10", 10, "#58a6ff"], ["MA20", 20, "#bc8cff"], ["MA60", 60, "#3fb950"]].map(([name, n, color]) => ({
          name, type: "line", data: slice(maAll[n]), xAxisIndex: 0, yAxisIndex: 0,
          showSymbol: false, lineStyle: { width: 1, color }, connectNulls: true, z: 3,
        })),
        { name: "成交量", type: "bar", data: vols, xAxisIndex: 1, yAxisIndex: 1, barWidth: "60%" },
      ],
    });

    klineChart.off("click");
    klineChart.on("click", (p) => {
      if (p.componentType === "markPoint" && p.data && p.data.itemId) { select(p.data.itemId); return; }
      if (p.componentType === "series" && p.seriesType === "candlestick" && p.name) {
        S.cursor = p.name; sync(); renderAll();
      }
    });
    // 用户手势/滚轮缩放后记录窗口；跨过标注密度阈值时（防抖）重渲染以显隐标注
    klineChart.off("datazoom");
    klineChart.on("datazoom", () => {
      const z = currentZoom();
      S.zoom = (z.start <= 0.01 && z.end >= 99.99) ? null : { start: z.start, end: z.end };
      const on = markerLabelsOn(barsDisplay().length);
      if (on !== S._labelsOn) {
        S._labelsOn = on;
        if (S._dt) clearTimeout(S._dt);
        S._dt = setTimeout(() => { if (S.site) renderKline(); }, 260);
      }
    });
  }

  function hexA(hex, a) {
    const r = parseInt(hex.slice(1, 3), 16), g = parseInt(hex.slice(3, 5), 16), b = parseInt(hex.slice(5, 7), 16);
    return "rgba(" + r + "," + g + "," + b + "," + a + ")";
  }

  function renderPhaseLegend() {
    $("phase-legend").innerHTML = S.site.phases.map((ph) =>
      '<span><i style="background:' + (PHASE_COLORS[ph.name] || "#8b949e") + '"></i>' + esc(ph.name) +
      " " + esc(ph.start) + "~" + esc(ph.end) +
      (ph.return != null ? ' <b class="' + signCls(ph.return) + '">' + fmtPctRaw(ph.return) + "</b>" : "") +
      (ph.max_drawdown != null ? ' <span class="sub">回撤' + ph.max_drawdown.toFixed(0) + "%</span>" : "") + "</span>"
    ).join("");
  }

  /* ── 右栏：详情卡 + as-of 信息包 ── */
  function renderDetail() {
    const el = $("detail-card");
    const it = tlAll().find((x) => x.id === S.selected);
    if (!it) {
      el.innerHTML = '<p class="sub">点击 K线标记 / 时间轴条目 查看事件详情（再点一次可取消）；点击 K线空白处移动光标；<a href="#" id="help-inline">使用说明</a></p>';
      const hl = $("help-inline");
      if (hl) hl.addEventListener("click", (e) => { e.preventDefault(); $("help-modal").classList.remove("hidden"); });
      return;
    }
    const a = it.as_of || {};
    let h = '<div class="detail-actions">' +
      (it.available_at !== S.cursor ? '<button data-act="move">◉ 光标移到此日</button>' : "") +
      (S.prevCursor ? '<button data-act="back">← 返回 ' + esc(S.prevCursor) + "</button>" : "") +
      (S.cursor !== S.site.meta.end_date ? '<button data-act="end">回到区间末</button>' : "") +
      '<button data-act="clear">✕ 取消选择</button></div>';
    h += '<div class="card"><div class="c-head"><span class="tbadge ' + esc(it.type) + '">' + esc(it.type) + "</span>" +
      '<span class="c-title">' + esc(it.title) + "</span></div>" +
      '<div class="c-meta">' + esc(TYPE_NAME[it.type] || it.type) + " · 事件日 " + esc(it.date) +
      " · 披露 " + esc(it.published_at || "—") + " · 可见 " + esc(it.available_at) +
      " · 重要性 " + esc(it.importance || "—") + ' <span class="dot ' + esc(it.confidence) + '"></span></div>';
    if (it.tags && it.tags.length) h += '<div class="c-meta">' + it.tags.map((t) => "#" + esc(t)).join(" ") + "</div>";
    h += '<div class="c-txt">' + esc(it.summary || "") + "</div>";
    if (a.summary) h += '<div class="sec-label">当时视角（as-of）</div><div class="c-txt">' + esc(a.summary) + "</div>";
    if (a.logic_state) h += '<div class="sec-label">逻辑状态 <span class="state ' + esc(a.logic_state) + '">' + esc(a.logic_state) + "</span></div>";
    if (a.evidence && a.evidence.length) h += '<div class="c-meta">证据: ' + a.evidence.map(esc).join(" · ") + "</div>";
    if (it.source_url) h += '<div class="c-src">来源: <a href="' + esc(it.source_url) + '" target="_blank" rel="noopener">' + esc(it.source || "原始出处") + " ↗</a></div>";
    if (!inAsOf()) {
      if (it.hindsight && it.hindsight.summary) h += '<div class="sec-label">事后视角</div><div class="c-txt">' + esc(it.hindsight.summary) + "</div>";
      if (it.hindsight && it.hindsight.verification) h += '<div class="c-meta">验证: ' + esc(it.hindsight.verification) + "</div>";
      if (it.price_reaction) h += renderReaction(it.price_reaction);
    } else {
      h += '<div class="c-meta" style="margin-top:6px">（事后信息与价格反馈需切换「事后视角」查看）</div>';
    }
    h += "</div>";
    el.innerHTML = h;
    el.querySelectorAll("button[data-act]").forEach((b) => b.addEventListener("click", () => {
      const act = b.dataset.act;
      if (act === "move") moveCursorTo(it.available_at);
      else if (act === "back") restoreCursor(S.prevCursor, false);
      else if (act === "end") restoreCursor(S.site.meta.end_date, false);
      else if (act === "clear") { S.selected = null; sync(); renderAll(); }
    }));
  }

  function renderReaction(pr) {
    if (pr.t1 == null && pr.t5 == null && pr.t20 == null && pr.t60 == null) return "";
    const cell = (v) => '<span class="' + signCls(v) + '">' + fmtPctRaw(v) + "</span>";
    let h = '<div class="sec-label">价格反馈（基准 ' + esc(pr.base_date) + " 收盘）</div>" +
      "<div class='react'><b>窗口</b><b>T+1</b><b>T+5</b><b>T+20</b><b>T+60</b>" +
      "<b>收益</b>" + cell(pr.t1) + cell(pr.t5) + cell(pr.t20) + cell(pr.t60) + "</div>";
    Object.keys(pr.excess || {}).forEach((tc) => {
      const ex = pr.excess[tc] || {};
      h += "<div class='react'><b>超额 " + esc(tc) + "</b>" + cell(ex.t1) + cell(ex.t5) + cell(ex.t20) + cell(ex.t60) + "</div>";
    });
    return h;
  }

  function renderInfoPackage() {
    const vis = tlAll().filter((it) => it.available_at <= S.cursor);
    const sameDay = vis.filter((it) => it.available_at === S.cursor);
    const recent = vis.filter((it) => {
      if (it.available_at === S.cursor) return false;
      const days = (new Date(S.cursor) - new Date(it.available_at)) / 86400000;
      const persist = (it.type === "E" || it.type === "R") ? E_R_PERSIST_DAYS : OTHER_PERSIST_DAYS;
      return days <= persist;
    }).sort((a, b) => (a.available_at < b.available_at ? 1 : -1)).slice(0, 30);

    const card = (it) => {
      const st = (it.as_of && it.as_of.logic_state) ? '<span class="state ' + esc(it.as_of.logic_state) + '">' + esc(it.as_of.logic_state) + "</span>" : "";
      return '<div class="card" style="cursor:pointer" data-id="' + esc(it.id) + '">' +
        '<div class="c-head"><span class="tbadge ' + esc(it.type) + '">' + esc(it.type) + "</span>" +
        '<span class="c-meta">' + esc(it.available_at) + '</span> <span class="dot ' + esc(it.confidence) + '"></span>' + st + "</div>" +
        '<div class="c-txt">' + esc(it.as_of && it.as_of.summary ? it.as_of.summary : it.summary) + "</div></div>";
    };
    let h = "";
    if (sameDay.length) h += '<div class="sec-label">当日新增（' + sameDay.length + "）</div>" + sameDay.map(card).join("");
    h += '<div class="sec-label">近期仍有效（' + recent.length + "）</div>" + (recent.map(card).join("") || '<p class="sub">—</p>');
    $("info-package").innerHTML = h;
    $("info-package").querySelectorAll(".card").forEach((el) =>
      el.addEventListener("click", () => select(el.dataset.id)));
  }

  /* ── 一致预期 ── */
  function renderConsensus() {
    if (!consensusChart) consensusChart = echarts.init($("consensus"), null, { renderer: "canvas" });
    const P = pal();
    const fys = Object.keys(S.site.consensus).sort();
    const palette = ["#58a6ff", "#3fb950", "#d29922", "#bc8cff", "#e3b341", "#79c0ff", "#ff7b72"];
    const series = fys.map((fy, i) => {
      let pts = S.site.consensus[fy];
      if (inAsOf()) pts = pts.filter((p) => p.d <= S.cursor);
      return {
        name: "FY" + fy, type: "line", showSymbol: true, symbolSize: 5,
        data: pts.map((p) => [p.d, +(p.eps).toFixed(3)]),
        lineStyle: { width: 1.5, color: palette[i % palette.length] },
        itemStyle: { color: palette[i % palette.length] },
      };
    }).filter((s) => s.data.length);
    consensusChart.setOption({
      animation: false, backgroundColor: "transparent",
      tooltip: { trigger: "axis", backgroundColor: P.ttBg, borderColor: P.ttBorder, textStyle: { color: P.ttText, fontSize: 12 }, confine: true, formatter: (ps) => {
        const d = ps[0].axisValue;
        const rows = ps.map((p) => p.seriesName + "：" + p.data[1] + " 元");
        const org = (S.site.consensus[ps[0].seriesName.replace("FY", "")] || []).filter((x) => x.d === d);
        return d + "<br/>" + rows.join("<br/>") + (org.length ? "<br/>机构数：" + org[0].n_org : "");
      } },
      legend: { textStyle: { color: P.text }, top: 0 },
      grid: { left: 46, right: 14, top: 26, bottom: 24 },
      xAxis: { type: "category", axisLabel: { color: P.text }, axisLine: { lineStyle: { color: P.line } } },
      yAxis: { scale: true, axisLabel: { color: P.text }, splitLine: { lineStyle: { color: P.grid } } },
      series,
    }, true);
    consensusChart.resize();
  }

  /* ── 估值 ── */
  function renderValuationChart() {
    if (!valChart) valChart = echarts.init($("valuation"), null, { renderer: "canvas" });
    const P = pal();
    let series = S.site.valuation.series;
    if (inAsOf()) series = series.filter((r) => r.d <= S.cursor);
    valChart.setOption({
      animation: false, backgroundColor: "transparent",
      tooltip: { trigger: "axis", backgroundColor: P.ttBg, borderColor: P.ttBorder, textStyle: { color: P.ttText, fontSize: 12 }, confine: true },
      legend: { textStyle: { color: P.text }, top: 0 },
      grid: { left: 46, right: 44, top: 26, bottom: 24 },
      xAxis: { type: "category", data: series.map((r) => r.d), axisLabel: { color: P.text }, axisLine: { lineStyle: { color: P.line } } },
      yAxis: [
        { scale: true, axisLabel: { color: P.text }, splitLine: { lineStyle: { color: P.grid } } },
        { min: 0, max: 100, axisLabel: { color: P.text, formatter: "{value}%" }, splitLine: { show: false } },
      ],
      series: [
        { name: "PE-TTM", type: "line", showSymbol: false, data: series.map((r) => r.pe), lineStyle: { width: 1.5, color: "#e3b341" }, itemStyle: { color: "#e3b341" } },
        { name: "PE 分位", type: "line", showSymbol: false, yAxisIndex: 1, data: series.map((r) => r.pe_pctile), lineStyle: { width: 1, color: "#58a6ff", opacity: 0.7 }, itemStyle: { color: "#58a6ff" } },
      ],
    }, true);
    valChart.resize();
  }

  /* ── 财报表 ── */
  function renderEarningsTable() {
    let periods = S.site.earnings;
    if (inAsOf()) periods = periods.filter((p) => p.report_date && p.report_date <= S.cursor);
    const rows = periods.map((p) => {
      const f = p.flat || {};
      const a = p.analysis || {};
      const cur = p.report_date === S.cursor ? " cur" : "";
      const state = a.logic_status ? '<span class="state ' + esc(a.logic_status) + '">' + esc(a.logic_status) + "</span>" : "—";
      return '<tr class="clickable' + cur + '" data-period="' + esc(p.period) + '" data-date="' + esc(p.report_date || "") + '">' +
        "<td class='l'>" + esc(p.period) + " <span class='sub'>" + esc(p.report_type) + "</span></td>" +
        "<td>" + esc(p.report_date || "—") + "</td>" +
        "<td>" + fmtYi(f.revenue_single) + "</td>" +
        '<td class="' + signCls(f.revenue_yoy) + '">' + fmtPct(f.revenue_yoy) + "</td>" +
        '<td class="' + signCls(f.revenue_qoq) + '">' + fmtPct(f.revenue_qoq) + "</td>" +
        "<td>" + fmtYi(f.parent_net_single) + "</td>" +
        '<td class="' + signCls(f.parent_net_yoy) + '">' + fmtPct(f.parent_net_yoy) + "</td>" +
        "<td>" + fmtPct(f.gross_margin_single) + "</td>" +
        "<td>" + fmtPct(f.net_margin_single) + "</td>" +
        "<td>" + fmtYi(f.ocf_single) + "</td>" +
        "<td>" + fmtNum(f.cash_content_single) + "</td>" +
        "<td>" + state + "</td>" +
        '<td><span class="dot ' + esc(p.confidence) + '"></span></td></tr>';
    }).join("");
    $("earnings-table").innerHTML =
      '<div style="max-height:320px;overflow:auto"><table><thead><tr>' +
      "<th class='l'>报告期</th><th>披露日</th><th>单季营收</th><th>营收YoY</th><th>营收QoQ</th><th>单季归母</th><th>归母YoY</th>" +
      "<th>毛利率</th><th>净利率</th><th>经营现金流</th><th>现金含量</th><th>逻辑状态</th><th>置信</th></tr></thead><tbody>" +
      (rows || "<tr><td colspan=13 class='l'>cursor 之前无可财报</td></tr>") + "</tbody></table></div>";
    $("earnings-table").querySelectorAll("tr.clickable").forEach((tr) =>
      tr.addEventListener("click", () => {
        const id = "E:" + tr.dataset.period;
        if (tlAll().some((x) => x.id === id)) select(id);
        else if (tr.dataset.date) moveCursorTo(tr.dataset.date);
      }));
  }

  /* ── 逻辑矩阵 ── */
  function renderLogic() {
    const items = S.site.logic_validation || [];
    const stateAt = (h) => {
      if (!inAsOf()) return h.state || (h.state_history.length ? h.state_history[h.state_history.length - 1].state : "—");
      const hist = (h.state_history || []).filter((s) => s.d <= S.cursor);
      return hist.length ? hist[hist.length - 1].state : "—";
    };
    $("logic-matrix").innerHTML = items.map((h) => {
      const st = stateAt(h);
      const hist = (h.state_history || []).filter((s) => !inAsOf() || s.d <= S.cursor);
      const chips = hist.map((s) => '<span class="evlink" data-d="' + esc(s.d) + '">' + esc(s.d) +
        ' <span class="state ' + esc(s.state) + '">' + esc(s.state) + "</span></span>").join("");
      return '<div class="logic-card"><div class="logic-head"><div><b>' + esc(h.id) + "</b> " + esc(h.hypothesis) + "</div>" +
        '<span class="state ' + esc(st) + '">' + esc(st) + "</span></div>" +
        (h.evidence ? '<div class="c-meta">证据: ' + esc(h.evidence) + "</div>" : "") +
        (h.price_feedback ? '<div class="c-meta">股价反馈: ' + esc(h.price_feedback) + "</div>" : "") +
        (h.next_checkpoint ? '<div class="c-meta">下一验证点: ' + esc(h.next_checkpoint) + "</div>" : "") +
        '<div class="logic-hist">' + (chips || "<span>（cursor 前暂无状态记录）</span>") + "</div></div>";
    }).join("") || "<p class='sub'>—</p>";
    $("logic-matrix").querySelectorAll(".evlink").forEach((el) =>
      el.addEventListener("click", () => { S.cursor = el.dataset.d; sync(); renderAll(); }));
  }

  /* ── 复盘结论 ── */
  function renderReview() {
    const panel = $("review-panel"), body = $("review-body");
    if (inAsOf()) { body.innerHTML = '<p class="sub">复盘结论基于事后信息，切换到「事后视角」查看。</p>'; return; }
    const r = S.site.review || {};
    const box = (title, arr) => arr && arr.length
      ? '<div class="review-box"><h4>' + esc(title) + "</h4><ul>" + arr.map((x) => "<li>" + esc(x) + "</li>").join("") + "</ul></div>" : "";
    body.innerHTML = '<div class="review-grid">' +
      box("做对了什么", r.what_worked) + box("做错了什么", r.what_failed) +
      box("最早信号", r.earliest_signals) + box("噪音", r.noise) +
      box("买点", r.buy_points) + box("卖点", r.sell_points) +
      "</div>" + box("经验教训", r.lessons);
  }

  /* ── 页脚 ── */
  function renderFooter() {
    fetch("data/data_sources.json").then((r) => r.json()).then((ds) => {
      $("footer-sources").innerHTML = '<span class="srcs">数据源：' + ds.sources.map((s) =>
        '<a href="' + esc(s.url) + '" target="_blank" rel="noopener">' + esc(s.name) + "</a>(" + esc(s.coverage) + " · 抓取 " + esc((s.fetch_time || "").slice(0, 10)) + ")").join(" · ") + "</span>";
      $("footer-gaps").innerHTML = '<span class="gaps">已知缺口：' + ds.gaps.map((g) =>
        '<span class="gap-chip" title="' + esc((g.reason || "") + " · 影响: " + (g.impact || "")) + '">' + esc(g.scope) + "</span>").join("") + "</span>";
    }).catch(() => {});
  }

  applyTheme();
  boot();
})();
