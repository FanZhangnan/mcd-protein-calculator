const $ = (id) => document.getElementById(id);
let BOOT = null;
let LAND = null;           // landscape payload for the current meal
let S = null;              // current result payload
let SSIG = "";             // signature of the current plan
let SREMOVED = {};         // { mainCode: [optionCode, ...] }
let SLAST = null;          // last valid payload
let WARN = {};             // { meal: "该时段返回的是另一餐次的菜单" }
let RANK_EXPANDED = false;

const MEAL_LABEL = { breakfast: "早餐", lunch: "午餐", dinner: "晚餐" };
const COMBOR = { nutrition_unknown: "营养未知", price_mismatch: "价格不一致",
  price_failed: "试算失败", membership: "会员价" };

async function api(path, body, method = "POST") {
  const opts = { method, headers: { "Content-Type": "application/json" } };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const resp = await fetch(path, opts);
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const err = new Error(data.error || `HTTP ${resp.status}`);
    err.kind = data.kind || "";
    err.status = resp.status;
    throw err;
  }
  return data;
}

const setStatus = (t) => { $("status").textContent = t || ""; };
const setSStatus = (t) => { $("s-status").textContent = t || ""; };
const num = (v, d = 1) => (v === null || v === undefined || Number.isNaN(Number(v))
  ? "—" : Number(v).toFixed(d));
// 营养数值：整数不带小数点，非整数保留一位（25.4 g）；价格仍用 num(v, 2)
const nutri = (v) => {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
  const n = Number(v);
  return Number.isInteger(n) ? String(n) : n.toFixed(1);
};
const isNarrow = () => window.matchMedia("(max-width: 600px)").matches;
const isOffline = () => (BOOT && BOOT.source) === "offline";
const isSnapshot = () => (BOOT && BOOT.source) === "snapshot";

function comboName(o) {
  if (!o) return "";
  if (Array.isArray(o.items) && o.items.length) return o.items.join(" + ");
  return o.name || "";
}

// ---------------------------------------------------------------- bootstrap
function renderDataSource() {
  const el = $("data-source");
  if (!el) return;
  const c = BOOT && BOOT.cache ? BOOT.cache : {};
  const s = BOOT && BOOT.state ? BOOT.state : {};
  const source = (BOOT && BOOT.source) || c.source || "none";
  const storeName = s.store && s.store.name ? s.store.name : "";
  const date = s.date || c.date || "—";
  const meal = $("s_meal") ? $("s_meal").value : "lunch";
  const mealLabel = MEAL_LABEL[meal] || meal;
  const mealTime = (s.meal_times && s.meal_times[meal]) || (c.meal_times && c.meal_times[meal]) || "—";

  let kind = "cache";
  let text = "";
  if (source === "offline") {
    kind = "offline";
    text = "离线样例数据，价格不是门店实时价";
  } else if (source === "snapshot") {
    kind = "snapshot";
    text = `快照数据 · ${storeName || "—"} · 抓取于 ${(c.fetched_at || "—").replace("T", " ").slice(0, 16)}`;
  } else if (source === "cache") {
    kind = "cache";
    text = `缓存数据 · 抓取于 ${c.fetched_at || "—"}`;
    if (storeName) text += ` · ${storeName}`;
    text += ` · ${date} ${mealLabel} ${mealTime}`;
  } else {
    kind = "live";
    text = `实时数据 · ${storeName || "—"} · ${date} ${mealLabel} ${mealTime}`;
  }
  el.setAttribute("data-source", kind);
  el.innerHTML = `<span class="src-dot src-${kind}"></span><span class="src-text">${text}</span>`;
}

async function boot() {
  BOOT = await api("/api/bootstrap", undefined, "GET");
  const s = BOOT.state;
  $("date").value = s.date || "";
  $("store_code").value = s.store?.code || "";
  $("t_breakfast").value = s.meal_times?.breakfast || "08:15";
  $("t_lunch").value = s.meal_times?.lunch || "12:15";
  $("t_dinner").value = s.meal_times?.dinner || "18:30";

  const c = BOOT.cache || {};
  WARN = c.menu_warnings || {};
  renderDataSource();
  renderMeta();
  renderUnknown();
  renderFoot();
}

function renderFoot() {
  const c = (BOOT && BOOT.cache) || {};
  const stamp = (BOOT && BOOT.fetched_at) || c.fetched_at || "";
  const el = $("foot-update");
  if (!el) return;
  el.textContent = `数据更新于 ${stamp || "—"}`;
}

function renderMeta() {
  const c = BOOT && BOOT.cache ? BOOT.cache : {};
  const s = BOOT && BOOT.state ? BOOT.state : {};
  const source = (BOOT && BOOT.source) || c.source || "none";
  const srcLabel = {
    live: "实时数据", cache: "缓存数据", offline: "离线样例数据",
    none: "未加载", unavailable: "不可用",
  }[source] || source;
  const cat = BOOT && BOOT.catalog ? BOOT.catalog : {};
  const rows = cat.rows || 0;
  const names = cat.unique_names || 0;
  const aliases = cat.aliases || 0;
  const dupes = cat.duplicates || 0;

  let html = "";
  html += `<div class="meta-row"><b>门店</b>${s.store?.name || "—"} <span class="mono">(${s.store?.code || "—"})</span></div>`;
  html += `<div class="meta-row"><b>日期</b>${s.date || "—"}</div>`;
  html += `<div class="meta-row"><b>餐次时间</b>早餐 ${s.meal_times?.breakfast || "—"} · 午餐 ${s.meal_times?.lunch || "—"} · 晚餐 ${s.meal_times?.dinner || "—"}</div>`;
  html += `<div class="meta-row"><b>数据来源</b>${srcLabel}${c.fetched_at ? ` · 抓取于 ${c.fetched_at}` : ""}</div>`;
  html += `<div class="meta-row"><b>营养条目</b>${rows} 条，${names} 个唯一名称，别名 ${aliases} 个，同名冲突 ${dupes} 个</div>`;
  html += `<div class="meta-row"><b>严格模式</b>营养只取官方整款标准版；去除配料只改点单，不改显示的数值。</div>`;
  if (c.error) html += `<div class="meta-row err"><b>错误</b>${c.error}</div>`;
  $("meta").innerHTML = html;
}

function renderUnknown() {
  const meal = $("s_meal").value;
  const unk = (BOOT && BOOT.unknown || {})[meal] || [];
  const rows = unk.map((u) => `<tr><td>${u.name}</td><td>${u.code}</td><td>${u.status}</td><td>${u.note || ""}</td></tr>`);
  if (rows.length) {
    $("unknown").innerHTML =
      `<details class="unknown-box">
         <summary>未纳入计算的商品：${unk.length} 个（点击查看列表）</summary>
         <div class="sub">本餐次以下 SKU 无法唯一匹配官方营养，<b>未纳入计算</b>（不会按 0 处理）：</div>
         <table class="unknown-table"><tr><th>名称</th><th>code</th><th>状态</th><th>原因</th></tr>${rows.join("")}</table>
       </details>`;
  } else {
    $("unknown").innerHTML =
      `<div class="meta-row"><b>未纳入计算的商品</b>0 个 · 本餐次参与计算的所有 SKU 均有可核实的官方营养来源。</div>`;
  }
}

// ------------------------------------------------------------------ landscape
async function loadLandscape() {
  const meal = $("s_meal").value;
  const maxMains = $("s_max_mains").value;
  try {
    LAND = await api(`/api/meal/landscape?meal=${encodeURIComponent(meal)}&max_mains=${maxMains}`,
      undefined, "GET");
  } catch (e) {
    LAND = null;
    $("rank-table").innerHTML = "";
    $("rank-cards").innerHTML = "";
    $("chart").innerHTML = `<text x="340" y="200" text-anchor="middle" class="tick">性价比图加载失败：${e.message}</text>`;
    return;
  }
  renderRank();
  drawChart();
  updateCapHint();
  renderUnknown();
  if (WARN[meal] && !S) setSStatus(WARN[meal]);
}

function renderRank() {
  const items = (LAND && LAND.items) || [];
  const shown = items.slice(0, RANK_EXPANDED ? 10 : 5);
  const planCodes = new Set((S && S.items || [])
    .filter((i) => i.kind === "main").map((i) => String(i.code)));

  if (!items.length) {
    $("rank-table").innerHTML = "<tr><td class='sub'>本餐次没有匹配到官方营养的主食。</td></tr>";
    $("rank-cards").innerHTML = "";
    $("rank-toggle").hidden = true;
    return;
  }

  const rows = shown.map((it, idx) => `
    <tr class="${idx < 3 ? "hot" : ""} ${planCodes.has(String(it.code)) ? "in-plan" : ""}">
      <td class="idx">${idx + 1}</td>
      <td class="name">${it.name}</td>
      <td class="num">${num(it.price, 2)}</td>
      <td class="num">${nutri(it.protein)}</td>
      <td class="num ppy">${num(it.protein_per_yuan, 1)}</td>
    </tr>`).join("");
  $("rank-table").innerHTML =
    `<tr><th class="idx">#</th><th class="name">主食</th><th>价格（元）</th><th>蛋白质（g）</th><th>每元蛋白质</th></tr>${rows}`;

  $("rank-cards").innerHTML = shown.map((it, idx) => `
    <div class="rank-card ${planCodes.has(String(it.code)) ? "in-plan" : ""}">
      <div class="rc-idx">${idx + 1}</div>
      <div class="rc-main">
        <div class="rc-name">${it.name}</div>
        <div class="rc-meta">${nutri(it.protein)} g · ${num(it.price, 2)} 元</div>
      </div>
      <div class="rc-ppy">${num(it.protein_per_yuan, 1)} g/元</div>
    </div>`).join("");

  const toggle = $("rank-toggle");
  toggle.hidden = items.length <= 5;
  toggle.textContent = RANK_EXPANDED ? "收起" : "查看全部 10 款";
}

// Build axis ticks that land on round numbers.
// maxValue  -> highest value that has to be visible (target, largest data point,
//              recommended plan; already max'ed by the caller)
// steps     -> candidate step sizes, e.g. [5, 10] for money, [5, 10, 15] for grams
// Returns { max, step, ticks } where ticks is a strictly increasing list of
// integers starting at 0, 4-6 of them, and max >= maxValue.
function niceTicks(maxValue, candidateSteps) {
  const raw = Number(maxValue);
  const safe = Number.isFinite(raw) && raw > 0 ? raw : 1;
  const steps = (candidateSteps && candidateSteps.length ? candidateSteps : [5, 10])
    .map(Number).filter((n) => Number.isFinite(n) && n > 0);
  const pool = steps.length ? steps : [5, 10];

  const target = safe * 1.1;
  // Prefer a step whose tick count lands in 4..6 ("4-6 ticks" is a guideline).
  // The hard requirement is max >= inputValue with a round step. When no step
  // gives 4..6 ticks, take the one with the fewest ticks instead — a coarse but
  // readable axis beats a 13-tick one.
  let best = null;
  for (const step of pool) {
    const max = Math.ceil(target / step) * step;
    const count = Math.round(max / step) + 1;
    const inRange = count >= 4 && count <= 6;
    const better = best === null
      || (inRange && !best.inRange)
      || (inRange === best.inRange && count < best.count);
    if (better) best = { max, step, count, inRange };
    if (inRange && count === 4) break;
  }
  const chosen = best === null ? { max: Math.ceil(target / pool[0]) * pool[0], step: pool[0] } : best;
  const { max, step } = chosen;
  const ticks = [];
  for (let v = 0; v <= max + 1e-9; v += step) ticks.push(Math.round(v));
  return { max, step, ticks };
}

const CHART_W = 680;
// 右侧留 26px：x 轴最右的刻度文字（居中）也不会贴到视口边缘（要求 ≥16px）
const PAD = { l: 56, r: 26, t: 24, b: 46 };
// a taller canvas on phones keeps the plot >= 240 px at 390 px wide
const chartHeight = () => (isNarrow() ? 520 : 420);

function planPoint(s) {
  if (!s || !s.items || !s.items.length || !(Number(s.cost) > 0)) return null;
  const t = s.totals || {};
  if (t.protein === null || t.protein === undefined) return null;
  return {
    name: `推荐方案：${comboName(s)}`,
    price: Number(s.cost), protein: Number(t.protein),
    fat: Number(t.fat), kcal: Number(t.energyKcal),
    ppy: s.protein_per_yuan, kind: "plan",
  };
}

function saverPoint(s) {
  if (!s || !s.saver) return null;
  const v = s.saver;
  if (v.protein === null || v.protein === undefined) return null;
  return {
    name: `省钱选项：${comboName(v)}`,
    price: Number(v.cost), protein: Number(v.protein),
    fat: Number(v.fat), kcal: Number(v.kcal),
    ppy: v.protein_per_yuan, kind: "saver",
  };
}

function starPath(cx, cy, outer, inner) {
  const pts = [];
  for (let i = 0; i < 10; i += 1) {
    const r = i % 2 === 0 ? outer : inner;
    const a = -Math.PI / 2 + (i * Math.PI) / 5;
    pts.push(`${(cx + r * Math.cos(a)).toFixed(1)},${(cy + r * Math.sin(a)).toFixed(1)}`);
  }
  return `M${pts.join("L")}Z`;
}

function drawChart() {
  const svg = $("chart");
  const H = chartHeight();
  svg.setAttribute("viewBox", `0 0 ${CHART_W} ${H}`);
  const items = (LAND && LAND.items) || [];
  if (!items.length) {
    svg.innerHTML = `<text x="340" y="${H / 2}" text-anchor="middle" class="tick">本餐次暂无可绘制的主食</text>`;
    return;
  }
  // 按实际显示宽度换算字号，保证 12.5px 左右，不随 viewBox 放大缩小
  const chartShown = svg.getBoundingClientRect().width || CHART_W;
  const fs = Math.max(10, Math.min(30, 12.5 * (CHART_W / chartShown)));
  svg.style.setProperty("--chart-fs", `${fs.toFixed(2)}px`);
  const target = Number($("s_protein").value);
  const budget = Number($("s_budget").value);
  const rec = planPoint(S);
  const saver = saverPoint(S);
  const tolerance = Number($("s_tolerance").value) || 0;

  const xs = items.map((d) => d.price).concat([Number.isFinite(budget) && budget > 0 ? budget : 0]);
  const ys = items.map((d) => d.protein).concat([Number.isFinite(target) && target > 0 ? target : 0]);
  [rec, saver].forEach((p) => { if (p) { xs.push(p.price); ys.push(p.protein); } });
  // price axis: steps of 5 / 10; protein axis: steps of 5 / 10 / 15
  const xTicks = niceTicks(Math.max(...xs), [5, 10]);
  const yTicks = niceTicks(Math.max(...ys), [5, 10, 15]);
  const xMax = xTicks.max;
  const yMax = yTicks.max;

  const x = (v) => PAD.l + (Math.min(v, xMax) / xMax) * (CHART_W - PAD.l - PAD.r);
  const y = (v) => H - PAD.b - (Math.min(v, yMax) / yMax) * (H - PAD.t - PAD.b);

  let g = "";
  xTicks.ticks.forEach((v) => {
    const px = x(v);
    g += `<line class="grid" x1="${px}" y1="${PAD.t}" x2="${px}" y2="${H - PAD.b}"/>`;
    g += `<text class="tick" x="${px}" y="${H - PAD.b + 15}" text-anchor="middle">${v}</text>`;
  });
  yTicks.ticks.forEach((v) => {
    const py = y(v);
    g += `<line class="grid" x1="${PAD.l}" y1="${py}" x2="${CHART_W - PAD.r}" y2="${py}"/>`;
    g += `<text class="tick" x="${PAD.l - 8}" y="${py + 3}" text-anchor="end">${v}</text>`;
  });
  g += `<line class="axis" x1="${PAD.l}" y1="${H - PAD.b}" x2="${CHART_W - PAD.r}" y2="${H - PAD.b}"/>`;
  g += `<line class="axis" x1="${PAD.l}" y1="${PAD.t}" x2="${PAD.l}" y2="${H - PAD.b}"/>`;
  g += `<text class="axis-label" x="20" y="${(PAD.t + H - PAD.b) / 2}" text-anchor="middle" transform="rotate(-90 20 ${(PAD.t + H - PAD.b) / 2})">蛋白质（g）</text>`;

  if (Number.isFinite(budget) && budget > 0 && Number.isFinite(target) && target > 0) {
    const x2 = Math.max(PAD.l + 1, x(budget));
    const y1 = Math.min(H - PAD.b - 1, y(target));
    g += `<rect class="region" data-w="${(x2 - PAD.l).toFixed(1)}" data-h="${(y1 - PAD.t).toFixed(1)}" x="${PAD.l}" y="${PAD.t}" width="${(x2 - PAD.l).toFixed(1)}" height="${(y1 - PAD.t).toFixed(1)}"/>`;
    // 容差带：目标线下方 tolerance 克的高度；说明文字放在带子最左端
    if (tolerance > 0) {
      const yLow = Math.min(H - PAD.b, y(target - tolerance));
      const h = Math.max(1, yLow - y1);
      g += `<rect class="band" data-w="${(x2 - PAD.l).toFixed(1)}" x="${PAD.l}" y="${y1.toFixed(1)}" width="${(x2 - PAD.l).toFixed(1)}" height="${h.toFixed(1)}"/>`;
      g += `<text class="band-label" x="${PAD.l + 8}" y="${(y1 + h / 2 + 4).toFixed(1)}" text-anchor="start">差 ${tolerance} g 以内</text>`;
    }
  }

  // 灰点先画；每元蛋白质前三只画一次黑点（半径 9）+ 白色描边 + 点内编号
  items.forEach((d, idx) => {
    if (idx < 3) return;
    g += `<circle class="dot" data-point="${idx}" cx="${x(d.price).toFixed(1)}" cy="${y(d.protein).toFixed(1)}" r="7"/>`;
  });
  const placed = [];
  items.slice(0, 3).forEach((d, idx) => {
    const tx = x(d.price);
    const ty = y(d.protein);
    let cx = tx;
    let cy = ty;
    const R = 9;
    const clash = placed.find((p) => Math.hypot(p.cx - tx, p.cy - ty) < 2 * R);
    if (clash) {
      const d0 = Math.hypot(clash.cx - tx, clash.cy - ty) || 1;
      const need = Math.sqrt(Math.max(0, Math.pow(2 * R + 2, 2) - d0 * d0));
      const step = Math.min(10, need / Math.SQRT2 + 1);
      cx = tx + step;
      cy = ty + step;
    }
    placed.push({ cx, cy });
    if (Math.abs(cx - tx) > 0.05 || Math.abs(cy - ty) > 0.05) {
      g += `<line class="dot-link" x1="${tx.toFixed(1)}" y1="${ty.toFixed(1)}" x2="${cx.toFixed(1)}" y2="${cy.toFixed(1)}"/>`;
    }
    const cxs = cx.toFixed(1);
    const cys = cy.toFixed(1);
    g += `<circle class="dot hot" data-point="${idx}" cx="${cxs}" cy="${cys}" r="${R}"/>`;
    g += `<text class="dot-num" data-num="${idx}" data-cx="${cxs}" data-cy="${cys}" data-real-x="${tx.toFixed(1)}" data-real-y="${ty.toFixed(1)}" x="${cxs}" y="${(Number(cys) + 4).toFixed(1)}">${idx + 1}</text>`;
  });

  // 悬停用：landscape 的字段是 protein_per_yuan，这里统一成 ppy
  const points = items.map((it) => ({
    ...it,
    ppy: it.protein_per_yuan !== undefined && it.protein_per_yuan !== null
      ? it.protein_per_yuan
      : (it.price > 0 ? it.protein / it.price : null),
  }));
  if (saver) {
    points.push(saver);
    const si = points.length - 1;
    const psx = x(saver.price);
    const psy = y(saver.protein);
    g += `<path class="saver-marker" data-point="${si}" data-x="${psx.toFixed(1)}" data-y="${psy.toFixed(1)}" d="${starPath(psx, psy, 11, 4.6)}"/>`;
    g += `<text class="star-label saver" data-cx="${psx.toFixed(1)}" data-cy="${psy.toFixed(1)}" data-r="11" x="${(psx - 17).toFixed(1)}" y="${(psy + 5).toFixed(1)}" text-anchor="end">省钱 ${Math.round(saver.protein)} g · ${Math.round(saver.price)} 元</text>`;
  }
  if (rec) {
    points.push(rec);
    const ri = points.length - 1;
    const prx = x(rec.price);
    const pry = y(rec.protein);
    g += `<path class="rec-marker" data-point="${ri}" data-x="${prx.toFixed(1)}" data-y="${pry.toFixed(1)}" data-cx="${prx.toFixed(1)}" data-cy="${pry.toFixed(1)}" d="${starPath(prx, pry, 12, 5)}"/>`;
    g += `<text class="star-label" data-cx="${prx.toFixed(1)}" data-cy="${pry.toFixed(1)}" data-r="12" x="${(prx - 18).toFixed(1)}" y="${(pry + 5).toFixed(1)}" text-anchor="end">推荐 ${Math.round(rec.protein)} g · ${Math.round(rec.price)} 元</text>`;
  }

  svg.innerHTML = g;
  placeStarLabels(svg);
  bindTips(svg, points);
  renderStarLegend();
}

// 星标文字优先放在星标左侧；放不下就换右侧、下方、上方。
// 任何文字都要留在绘图区内（右侧至少 16px），且不能压住「差 N g 以内」。
function placeStarLabels(svg) {
  const labels = [...svg.querySelectorAll("text.star-label")];
  if (!labels.length) return 0;
  const H = Number(svg.viewBox && svg.viewBox.baseVal ? svg.viewBox.baseVal.height : 0) || chartHeight();
  const rightLimit = CHART_W - 16;
  const bandLabel = svg.querySelector("text.band-label");
  const bandBox = bandLabel ? bboxOf(bandLabel) : null;
  const dotBoxes = [...svg.querySelectorAll("circle.dot")].map(bboxOf).filter(Boolean);
  const placed = [];
  labels.forEach((el) => {
    const cx = Number(el.dataset.cx);
    const cy = Number(el.dataset.cy);
    const r = Number(el.dataset.r) || 11;
    const candidates = [
      { anchor: "end", x: cx - r - 6, y: cy + 5 },
      { anchor: "start", x: cx + r + 6, y: cy + 5 },
      { anchor: "middle", x: cx, y: Math.min(cy + r + 17, H - PAD.b - 4) },
      { anchor: "middle", x: cx, y: Math.max(cy - r - 9, PAD.t + 12) },
    ];
    for (const c of candidates) {
      el.setAttribute("text-anchor", c.anchor);
      el.setAttribute("x", c.x.toFixed(1));
      el.setAttribute("y", c.y.toFixed(1));
      const box = bboxOf(el);
      if (!box) continue;                                  // 手机上文字被隐藏
      if (box.l < PAD.l + 1 || box.r > rightLimit) continue;
      if (box.t < 2 || box.b > H - 6) continue;
      if (bandBox && boxesHit(box, bandBox)) continue;
      if (dotBoxes.some((d) => boxesHit(box, d, 1))) continue;
      if (placed.some((p) => boxesHit(box, p))) continue;
      placed.push(box);
      break;
    }
  });
  return placed.length;
}

function bboxOf(el) {
  try {
    const b = el.getBBox();
    return b.width > 0 && b.height > 0
      ? { l: b.x, t: b.y, r: b.x + b.width, b: b.y + b.height } : null;
  } catch (e) { return null; }
}

function boxesHit(a, b, pad = 2) {
  return !(a.r + pad < b.l || a.l - pad > b.r || a.b + pad < b.t || a.t - pad > b.b);
}

function renderStarLegend() {
  const rec = planPoint(S);
  const saver = saverPoint(S);
  const r = $("sl-rec");
  const v = $("sl-save");
  if (r) r.textContent = rec
    ? `${Math.round(rec.protein)} g · ${Math.round(rec.price)} 元`
    : "—";
  if (v) v.textContent = saver
    ? `${Math.round(saver.protein)} g · ${Math.round(saver.price)} 元`
    : "—";
}

function bindTips(svg, points) {
  const tip = $("chart-tip");
  const show = (ev, idx) => {
    const row = points[idx];
    if (!row) return;
    tip.hidden = false;
    tip.innerHTML = `<b>${row.name}</b><br>价格 ${num(row.price, 2)} 元 · 蛋白质 ${nutri(row.protein)} g<br>
      脂肪 ${nutri(row.fat)} g · 热量 ${num(row.kcal, 0)} kcal<br>每元蛋白质 ${num(row.ppy, 1)} g`;
    const rect = svg.getBoundingClientRect();
    const px = ev.clientX !== undefined ? ev.clientX - rect.left : 0;
    const py = ev.clientY !== undefined ? ev.clientY - rect.top : 0;
    tip.style.left = `${Math.max(6, px + 12)}px`;
    tip.style.top = `${Math.max(6, py + 12)}px`;
  };
  svg.querySelectorAll("[data-point]").forEach((el) => {
    const idx = Number(el.dataset.point);
    el.addEventListener("mouseenter", (ev) => show(ev, idx));
    el.addEventListener("mousemove", (ev) => show(ev, idx));
    el.addEventListener("mouseleave", () => { tip.hidden = true; });
    el.addEventListener("click", (ev) => { ev.stopPropagation(); show(ev, idx); });
  });
}

// --------------------------------------------------------------- body estimate
function singleCalc() {
  const sex = $("s_sex").value;
  const age = Number($("s_age").value);
  const h = Number($("s_height").value);
  const w = Number($("s_weight").value);
  const act = Number($("s_activity").value);
  const meals = Math.max(1, Number($("s_meals").value) || 3);
  const goal = $("s_goal").value;
  if (![age, h, w, act].every(Number.isFinite) || age <= 0 || h <= 0 || w <= 0) {
    $("s-calc-note").textContent = "请先填写有效的年龄、身高、体重。";
    return;
  }
  const bmr = 10 * w + 6.25 * h - 5 * age + (sex === "male" ? 5 : -161);
  const tdee = bmr * act;
  const daily = goal === "muscle" ? tdee + 300 : tdee;
  const perKcal = daily / meals;
  const protein = (goal === "muscle" ? 2.0 : 1.6) * w / meals;
  $("s_protein").value = Math.round(protein);
  invalidateResult("已按身体数据填入蛋白质下限，请重新算这一餐。");
  $("s-calc-note").textContent =
    `BMR ${bmr.toFixed(0)} kcal，TDEE ${tdee.toFixed(0)} kcal，`
    + `${goal === "muscle" ? "增肌" : "维持"}参考 ${daily.toFixed(0)} kcal/天，每餐约 ${perKcal.toFixed(0)} kcal；`
    + `蛋白质 ${protein.toFixed(1)} g（${goal === "muscle" ? "2.0" : "1.6"} g/kg ÷ ${meals} 餐），已填入蛋白质下限。`
    + "热量仅为参考，未作为约束。";
  updateCapHint();
  drawChart();
}

function invalidateResult(message) {
  S = null; SLAST = null; SSIG = ""; SREMOVED = {};
  $("s-result-panel").hidden = true;
  setSStatus(message || "参数已变化，请重新算这一餐。");
  renderRank();
  drawChart();
}

// --------------------------------------------------------------------- inputs
function singleRequest() {
  const body = {
    meal: $("s_meal").value,
    protein_min: Number($("s_protein").value),
    budget: Number($("s_budget").value),
    max_mains: Number($("s_max_mains").value),
    allow_side: $("s_side").checked,
    protein_tolerance: Number($("s_tolerance").value),
  };
  const optional = [["s_fat", "fat_max"], ["s_kcal_min", "kcal_min"], ["s_kcal_max", "kcal_max"]];
  optional.forEach(([id, key]) => {
    const raw = $(id).value.trim();
    if (raw !== "") body[key] = Number(raw);
  });
  return body;
}

function singleValidate(body) {
  if (!body.meal) return "请选择餐次";
  for (const [key, label] of [["protein_min", "蛋白质下限"], ["budget", "价格上限"]]) {
    if (!Number.isFinite(body[key])) return `${label}必须是数字`;
    if (body[key] < 0) return `${label}不能是负数`;
  }
  if (body.budget <= 0) return "价格上限必须大于 0";
  if (!(body.protein_min > 0)) return "蛋白质下限必须大于 0";
  for (const [key, label] of [["fat_max", "脂肪上限"], ["kcal_min", "热量下限"], ["kcal_max", "热量上限"]]) {
    if (body[key] !== undefined && (!Number.isFinite(body[key]) || body[key] < 0)) {
      return `${label}必须是不小于 0 的数字`;
    }
  }
  if (body.kcal_min !== undefined && body.kcal_max !== undefined
      && body.kcal_max <= body.kcal_min) return "热量上限必须大于下限";
  if (![1, 2, 3].includes(body.max_mains)) return "主食数量上限只能是 1、2 或 3";
  if (!Number.isInteger(body.protein_tolerance)
      || body.protein_tolerance < 0 || body.protein_tolerance > 10) {
    return "可接受少几克蛋白质只能是 0–10 的整数";
  }
  return "";
}

// --------------------------------------------------------------------- render
function singleQuoteLine(q) {
  if (!q) return "未试算 · 仅参考价";
  if (q.status === "ok") {
    return `试算价 ${q.amount} 元（查询于 ${q.queried_at || "—"}，取餐时点 ${q.reservation_date || "—"}）`;
  }
  return `试算失败：${q.message || q.status}`;
}

function statusPill(s) {
  const status = s.protein_status || (s.feasible ? "met" : "under");
  if (status === "met") return '<span class="badge official">达标</span>';
  if (status === "near") return `<span class="badge near">接近达标 · 少 ${nutri(s.protein_gap)} g</span>`;
  return '<span class="badge cap">预算内最多</span>';
}

// 顶部数字区：蛋白质 / 价格 / 每元蛋白质
function metricsBlock(s, priceText) {
  const t = s.totals || {};
  const status = s.protein_status || (s.feasible ? "met" : "under");
  const ppy = s.protein_per_yuan;
  const ppyText = ppy === null || ppy === undefined ? "—" : num(ppy, 1);
  const capCls = status === "under" ? " cap" : "";
  return `<div class="metrics${capCls}">
      <div class="metric protein"><span class="metric-num">${nutri(t.protein)}</span><span class="metric-unit">g 蛋白质</span></div>
      <div class="metric price"><span class="metric-num">${priceText}</span><span class="metric-unit">元</span></div>
      <div class="metric ppy"><span class="metric-num">${ppyText}</span><span class="metric-unit">g/元 · 每元蛋白质</span></div>
    </div>
    <div class="blocks">
      <div class="block"><div class="b-label">热量</div><div class="b-value">${num(t.energyKcal, 0)}</div><div class="b-label">kcal</div></div>
      <div class="block"><div class="b-label">脂肪</div><div class="b-value">${nutri(t.fat)}</div><div class="b-label">g</div></div>
      <div class="block"><div class="b-label">碳水</div><div class="b-value">${nutri(t.carbohydrate)}</div><div class="b-label">g</div></div>
      <div class="block ppy-block"><div class="b-label">每元蛋白质</div><div class="b-value">${ppyText}</div><div class="b-label">g/元</div></div>
    </div>`;
}

// 进度条：填充 = min(实际/目标,1)，目标位置用 2px 黑竖线标出
// 左：目标与差额；右：预算与余额
function progressBlock(s, price) {
  const t = s.totals || {};
  const target = Number($("s_protein").value);
  const actual = Number(t.protein);
  const status = s.protein_status || (s.feasible ? "met" : "under");
  const gap = Number(s.protein_gap);
  const tol = Number(s.protein_tolerance || 0);
  const budget = Number($("s_budget").value);
  const scale = Math.max(target || 0, actual || 0, 1);
  const fill = Math.max(0, Math.min(actual / scale, 1)) * 100;
  const mark = Math.max(0, Math.min((target || 0) / scale, 1)) * 100;

  let leftText;
  if (status === "met") {
    const extra = Math.max(0, Math.round((actual - target) * 10) / 10);
    leftText = `目标 ${nutri(target)} g，多出 ${nutri(extra)} g`;
  } else if (status === "near") {
    leftText = `目标 ${nutri(target)} g，还差 ${nutri(gap)} g（在允许的 ${nutri(tol)} g 内）`;
  } else {
    leftText = `目标 ${nutri(target)} g，还差 ${nutri(gap)} g（超出允许的 ${nutri(tol)} g）`;
  }

  let rightText = "";
  if (Number.isFinite(budget) && budget > 0 && Number.isFinite(price) && price > 0) {
    const left = Math.round((budget - price) * 100) / 100;
    if (left > 0.005) rightText = `预算 ${nutri(budget)} 元，还剩 ${num(left, 2)} 元`;
    else if (left < -0.005) rightText = `预算 ${nutri(budget)} 元，超出 ${num(-left, 2)} 元`;
    else rightText = `预算 ${nutri(budget)} 元，刚好用完`;
  }
  return `<div class="progress-wrap">
      <div class="progress ${status === "under" ? "cap" : ""}">
        <div class="fill" style="width:${fill.toFixed(1)}%"></div>
        <span class="mark" style="left:${mark.toFixed(1)}%"></span>
      </div>
      <div class="progress-foot"><span class="left">${leftText}</span><span>${rightText}</span></div>
    </div>`;
}

function comboRoundsText(combo) {
  const rounds = (combo && combo.rounds) || [];
  return rounds.map((r) => `${r.role}：${r.name}`).join(" · ");
}

function itemRowsHtml(items) {
  return items.map((i) => `
    <div class="item">
      <div class="qty">×${i.quantity}</div>
      <div class="item-main">
        <div class="item-name">${i.name}${i.kind === "combo" ? ' <span class="combo-tag">套餐</span>' : ""}</div>
        <div class="item-sub">${num(i.price_ref, 2)} 元/份 · 蛋白质 ${nutri(i.nutrition?.protein)} g</div>
        ${i.kind === "combo" ? `<div class="combo-parts">${comboRoundsText(i.combo)}</div>` : ""}
        <details class="item-detail">
          <summary>明细</summary>
          <div class="sub">${num(i.nutrition?.energyKcal, 0)} kcal · 蛋白质 ${nutri(i.nutrition?.protein)} g ·
            脂肪 ${nutri(i.nutrition?.fat)} g · 碳水 ${nutri(i.nutrition?.carbohydrate)} g ·
            钠 ${num(i.nutrition?.sodium, 0)} mg</div>
          ${(i.removal_notes || []).map((n) => `<div class="strict-note">${n}</div>`).join("")}
          ${i.modification && i.modification.length
            ? `<div class="sub">点单参数：${i.modification.map((m) => `${m.code}:${m.key}`).join("，")}</div>` : ""}
        </details>
      </div>
      <div class="item-total">${num(i.line_total_ref, 2)}</div>
    </div>`).join("");
}

function renderSingle(s) {
  if (!s) { $("s-result-panel").hidden = true; return; }
  const items = s.items || [];
  const combos = s.combos || {};
  const comboInfo = combos.catalog || {};
  const comboApplied = !!(combos.applied || items.some((i) => i.kind === "combo"));
  const t = s.totals || {};
  const q = s.quotes && s.quotes[s.meal];
  const main = items.find((i) => i.kind === "main");
  const quoted = q && q.status === "ok";
  const hasItems = items.length > 0;
  const couponQuote = s.coupon_quote
    || (comboApplied ? { status: "unsupported", note: "套餐暂不支持券后价；上方价格是门店原价试算。" } : null);
  const couponOk = !!(couponQuote && couponQuote.status === "ok" && couponQuote.coupon);
  const basePrice = quoted ? Number(q.amount) : Number(s.cost || 0);
  const price = couponOk ? Number(couponQuote.coupon.amount) : basePrice;
  const priceText = hasItems ? num(price, 2) : "—";
  const status = s.protein_status || (s.feasible ? "met" : "under");
  const mealLabel = s.meal_label || MEAL_LABEL[s.meal] || "这一餐";

  const priceSide = !hasItems
    ? `<span class="price-tag">未选到商品</span>`
    : couponOk
      ? `<span class="price-tag quoted">券后价</span>`
      : quoted
        ? `<span class="price-tag quoted">已试算</span><span class="price-time">· 查询于 ${q.queried_at || "—"}</span>`
        : isSnapshot()
          ? `<span class="price-tag">快照参考价 · 未试算</span>`
          : `<span class="price-tag">参考价，未试算</span>`;

  const itemRows = itemRowsHtml(items);

  const mainCount = items.filter((i) => i.kind === "main").length;
  const suggestions = (s.suggestions || []).map((o) => {
    const target = String(o.target_code || main?.code || "");
    const checked = (SREMOVED[target] || []).includes(String(o.code));
    const text = `去掉 ${o.name}${mainCount > 1 ? `（${o.target_name || target}）` : ""}
        ${o.muscle_warning ? `<span class="muscle-warn">${o.muscle_note}</span>` : ""}`;
    if (isSnapshot()) return `<div class="sugg-static"><span>${text}</span></div>`;
    return `<label class="sugg">
      <input type="checkbox" class="srem" data-code="${o.code}" data-target="${target}" ${checked ? "checked" : ""}>
      <span>${text}</span>
    </label>`;
  }).join("");
  const suggNames = (s.suggestions || []).map((o) => o.name).filter(Boolean);
  const suggSummary = suggNames.length
    ? `点单建议：可去 ${suggNames.slice(0, 3).join("、")}${suggNames.length > 3 ? " 等" : ""}`
    : "点单建议：暂无可去配料";

  const upgradeList = (s.upgrades && s.upgrades.length) ? s.upgrades : (s.upgrade ? [s.upgrade] : []);
  const upgrade = upgradeList.map((u) => {
    const over = u.fits === false;
    return `<div class="upgrade ${over ? "over" : ""}">
      <div class="up-head"><b>升级一档：${u.label}</b>
        ${u.fit_note ? `<span class="fit ${over ? "over" : "ok"}">${u.fit_note}</span>` : ""}</div>
      <div>+${nutri(u.delta_protein)} g 蛋白质 · +${num(u.delta_price, 2)} 元 · +${nutri(u.delta_fat)} g 脂肪</div>
      ${u.show_conversion ? `<div class="sub">每多 1 元，多 ${num(u.protein_per_extra_yuan, 1)} g 蛋白质。</div>` : ""}
      <div class="sub">依据：${u.evidence || "两款均为官方整款数据"}</div>
    </div>`;
  }).join("");

  const saver = s.saver;
  // 券后价只展示门店试算结果；没有券或没降价时明确写「暂无可用券」
  let couponLine = "";
  if (!isSnapshot() && hasItems) {
    if (couponOk) {
      const c = couponQuote.coupon;
      couponLine = `<div class="coupon-line ok">
        <b>券后 ${num(c.amount, 2)} 元</b>（用 ${c.title}）· 原价 <s>${num(c.original_amount != null ? c.original_amount : basePrice, 2)} 元</s>${couponQuote.protein_per_yuan != null ? ` · 券后每元 ${num(couponQuote.protein_per_yuan, 1)} g 蛋白质` : ""}
        <span class="coupon-note">券后价来自门店试算，下单时以 App 为准</span>
      </div>`;
    } else if (couponQuote) {
      couponLine = `<div class="coupon-line muted">${
        couponQuote.status === "none" ? "暂无可用券"
          : couponQuote.status === "unsupported" ? (couponQuote.note || "套餐暂不支持券后价")
            : "券后价暂不可用"}</div>`;
    } else {
      couponLine = `<div class="coupon-line muted" id="coupon-pending">正在查询门店券…</div>`;
    }
  }
  // 省钱选项：标题与目标比，正文写清方案本身 + 与推荐方案相比省多少
  const saverGap = saver ? Number(saver.protein_gap || 0) : 0;
  const saverTol = Number(s.protein_tolerance || 0);
  const saverRow = (saver && status === "met") ? `<div class="saver-row">
      <div class="saver-body">
        <div class="saver-title">${saverGap > 0
          ? `省钱选项 · 比目标少 ${nutri(saverGap)} g，在你允许的 ${nutri(saverTol)} g 以内`
          : "省钱选项 · 同样达标"}</div>
        <div><b>${comboName(saver)}</b> · ${nutri(saver.protein)} g · ${num(saver.cost, 2)} 元，比推荐方案省 ${num(saver.money_saved, 2)} 元</div>
      </div>
      <button class="outline tiny" id="s-saver">换成这个</button>
    </div>` : "";

  // 预算内最多：加预算卡片 + 放宽脂肪卡片 + 浅灰说明（不再用红色提示框）
  const f = s.frontier || {};
  let underCard = "";
  if (status === "under") {
    const rp = f.reach_price;
    const cap = f.max_protein;
    const rf = f.relax_fat;
    const fatMax = s.targets ? s.targets.fat_max : null;
    const budget = Number($("s_budget").value);

    const card = (rp && hasItems) ? `<div class="under-card">
        <div class="under-text">加预算到 <b>${num(rp.cost, 2)} 元</b> · ${comboName(rp)} · <b>${nutri(rp.protein)} g</b></div>
        <button class="black" id="s-budget-up" data-cost="${rp.cost}">按 ${num(rp.cost, 0)} 元重新算</button>
      </div>` : "";
    const relaxFat = (rf && fatMax !== null && fatMax !== undefined) ? `<div class="under-card relax-card">
        <div class="under-text">放宽脂肪到 <b>${nutri(rf.fat)} g</b> · ${comboName(rf)} · <b>${nutri(rf.protein)} g</b> · ${num(rf.cost, 2)} 元</div>
        <button class="black" id="s-fat-up" data-fat="${rf.fat}">按 ${nutri(rf.fat)} g 脂肪重新算</button>
      </div>` : "";
    const note = cap ? `<div class="under-note">
        ${nutri(budget)} 元以内，这家店${mealLabel}最多只能做到 <b>${nutri(cap.protein)} g</b>。
        降低目标到 ${nutri(cap.protein)} g 也可以直接达标。</div>` : "";
    underCard = card + relaxFat + note;
  }

  const comboAudit = comboInfo.available ? (() => {
    const r = comboInfo.reasons || {};
    const parts = [];
    if (r.nutrition_unknown) parts.push(`营养未知 ${r.nutrition_unknown}`);
    if (r.price_mismatch) parts.push(`价格不一致 ${r.price_mismatch}`);
    if (r.price_failed) parts.push(`试算失败 ${r.price_failed}`);
    if (r.membership) parts.push(`会员价 ${r.membership}`);
    const list = (comboInfo.items || []).slice(0, 12).map((x) =>
      `<li>${x.name}${x.variant === "muscle" ? "（增肌组合）" : ""} · ${COMBOR[x.reason] || x.reason}${x.detail ? ` · ${x.detail}` : ""}</li>`).join("");
    return `<details class="combo-audit"><summary>套餐核查：已查 ${comboInfo.checked || 0} 个 SKU，识别 ${comboInfo.combos_found || 0} 个套餐，纳入 ${comboInfo.variants_used || 0} 个变体；排除 ${comboInfo.excluded || 0} 个${parts.length ? `（${parts.join("、")}）` : ""}</summary>
      <ul>${list}</ul>
      <div class="sub">${comboInfo.note || ""}</div>
    </details>`;
  })() : "";

  $("s-result").innerHTML = `
    <div class="single-card">
      <div class="single-head">
        <div class="head-left">
          ${statusPill(s)}
          <span class="card-sub">${mealLabel}推荐 · 官方标准版营养</span>
        </div>
        <div class="price-side">${priceSide}</div>
      </div>
      ${metricsBlock(s, priceText)}
      ${progressBlock(s, hasItems ? price : NaN)}
      ${couponLine}
      ${itemRows}
      ${comboApplied ? `<div class="combo-note">${combos.unsupported || "套餐暂不支持去配料与券后价"}</div>` : ""}
      ${saverRow}
      ${underCard}
      <div class="quote-row">
        ${isSnapshot()
          ? `<div class="quote-note">在线版不连接门店。实时价格和报价请在本地运行，或在 WorkBuddy 中使用 Skill。<a href="https://github.com/FanZhangnan/mcd-protein-calculator#安装" target="_blank" rel="noopener">安装说明</a></div>`
          : `<button id="s-quote" class="black" title="只按当前这一餐的商品/定制试算，不会重新选餐">为这一餐报价${isOffline() ? "（离线桩报价）" : ""}</button>`}
        ${suggestions ? `<details class="sugg-box"><summary>${suggSummary}</summary>
          <div class="sugg-list">${suggestions}</div>
          <div class="sub">去掉配料只写进点单参数；营养数值仍为标准版官方数据。</div></details>` : ""}
      </div>
      ${upgrade ? `<details class="upgrade-box"><summary>肉量升级（整款替换）</summary>${upgrade}</details>` : ""}
      ${comboAudit}
      <div class="sub strict-foot">严格模式：营养一律取官方整款标准版；去除配料只影响点单参数，不扣减任何数值。</div>
    </div>`;
  $("s-result-panel").hidden = false;

  document.querySelectorAll("input.srem").forEach((el) => {
    el.addEventListener("change", () => {
      const code = String(el.dataset.target || (main ? main.code : ""));
      if (!code) return;
      const set = new Set(SREMOVED[code] || []);
      if (el.checked) set.add(String(el.dataset.code));
      else set.delete(String(el.dataset.code));
      SREMOVED[code] = [...set];
      singleRecompute();
    });
  });
  const saverBtn = $("s-saver");
  if (saverBtn) saverBtn.onclick = () => singlePlan({ select: "saver" });
  const quoteBtn = $("s-quote");
  if (quoteBtn) quoteBtn.onclick = singleQuote;
  const upBtn = $("s-budget-up");
  if (upBtn) upBtn.onclick = () => {
    const cost = Number(upBtn.dataset.cost);
    if (!Number.isFinite(cost) || cost <= 0) return;
    $("s_budget").value = cost;
    updateCapHint();
    singlePlan();
  };
  const fatBtn = $("s-fat-up");
  if (fatBtn) fatBtn.onclick = () => {
    const fat = Number(fatBtn.dataset.fat);
    if (!Number.isFinite(fat) || fat <= 0) return;
    $("s_fat").value = fat;
    updateOptionsSummary();
    singlePlan();
  };

  renderRank();
  drawChart();
  if (!isSnapshot() && hasItems && !comboApplied && !s.coupon_checked
      && s.coupon_allowed !== false) loadCouponQuote(s);
}

async function loadCouponQuote(s) {
  if (s._couponLoading) return;
  s._couponLoading = true;
  try {
    const list = await api(`/api/coupons?meal=${encodeURIComponent(s.meal)}`, undefined, "GET");
    if (!(list.coupons || []).length) {
      s.coupon_quote = { status: "none", note: list.note || "" };
    } else {
      s.coupon_quote = await api("/api/quote_with_coupon", { meal: s.meal, signature: SSIG });
    }
  } catch (e) {
    s.coupon_quote = { status: "unavailable", error: String(e.message || e) };
  }
  s.coupon_checked = true;
  s._couponLoading = false;
  if (S === s) renderSingle(s);
}

// ---------------------------------------------------------------------- flows
async function singlePlan(extra, opts = {}) {
  const body = singleRequest();
  const bad = singleValidate(body);
  if (bad) { setSStatus(bad); return; }
  setSStatus("正在枚举这一餐的全部候选…");
  try {
    const out = await api("/api/meal", { ...body, ...(extra || {}), quote: false });
    out.coupon_allowed = opts.coupon !== false;
    out.auto = !!opts.auto;
    S = out; SLAST = out; SSIG = out.signature || "";
    SREMOVED = {};
    renderSingle(S);
    const gap = Number(out.protein_gap || 0);
    const tol = Number(out.protein_tolerance || 0);
    const head = out.context_warning ? `${out.context_warning} ` : "";
    let msg;
    if (out.protein_status === "near") {
      msg = `接近达标：比目标少 ${nutri(gap)} g，在你允许的 ${nutri(tol)} g 内（参考价 ${num(out.cost, 2)} 元）。`;
    } else if (out.protein_status === "under") {
      msg = "";   // 结果卡里已经说清楚了，不再重复提示
    } else if (out.selected === "saver") {
      msg = `已切换为省钱方案（参考价 ${num(out.cost, 2)} 元）；旧报价已失效，请重新报价。`;
    } else {
      msg = `已找到达标方案（参考价 ${num(out.cost, 2)} 元）。`
        + (out.saver ? " 下方还有一个更便宜的省钱选项。" : "");
    }
    setSStatus(head + msg);
  } catch (e) {
    S = SLAST;
    setSStatus("计算失败：" + e.message);
  }
}

async function singleRecompute() {
  if (!S) return;
  const mains = (S.items || []).filter((i) => i.kind === "main");
  if (!mains.length) return;
  const custom = {};
  mains.forEach((m) => {
    const codes = SREMOVED[String(m.code)] || [];
    if (!codes.length) return;
    custom[S.meal] = custom[S.meal] || {};
    custom[S.meal][m.code] = { action: "suggest_remove", option_codes: codes };
  });
  const hasCustom = Object.keys(custom).length > 0;
  try {
    const out = await api("/api/recompute", { customizations: custom });
    S = { ...S, ...out, removable: S.removable, suggestions: S.suggestions,
          upgrade: S.upgrade, upgrades: S.upgrades, frontier: S.frontier,
          meal: S.meal, meal_label: S.meal_label,
          coupon_quote: null, coupon_checked: false };
    SLAST = S; SSIG = out.signature || "";
    renderSingle(S);
    setSStatus(hasCustom
      ? "已加入点单建议。营养仍为标准版官方数值；旧试算价已失效，请重新报价。"
      : "已取消点单建议，恢复标准版。");
  } catch (e) {
    setSStatus("点单建议未生效：" + e.message);
    renderSingle(S);
  }
}

async function singleQuote() {
  if (!S || !SSIG) {
    setSStatus("请先点「算这一餐」；报价只针对当前这一餐，不会重新选餐。");
    return;
  }
  setSStatus("正在为当前这一餐试算…");
  try {
    const out = await api("/api/quote", { signature: SSIG });
    S = { ...S, ...out, removable: S.removable, suggestions: S.suggestions,
          upgrade: S.upgrade, upgrades: S.upgrades, frontier: S.frontier,
          meal: S.meal, meal_label: S.meal_label,
          coupon_quote: null, coupon_checked: false };
    SLAST = S; SSIG = out.signature || "";
    renderSingle(S);
    const q = out.quotes && out.quotes[S.meal];
    setSStatus(q && q.status === "ok"
      ? `试算成功：${q.amount} 元（查询于 ${q.queried_at || "—"}；取餐时点 ${q.reservation_date || "—"}）。`
      : `试算失败：${(q && q.message) || "接口未返回金额"}。`);
  } catch (e) {
    setSStatus("试算失败：" + e.message);
  }
}

function updateCapHint() {
  const budget = Number($("s_budget").value);
  if (!LAND || !Number.isFinite(budget) || budget <= 0) {
    $("s-cap-hint").textContent = "";
    return;
  }
  let best = null;
  (LAND.frontier || []).forEach((p) => { if (p.price <= budget + 1e-9) best = p; });
  $("s-cap-hint").textContent = best
    ? `这家店这一餐，在价格 ≤ ${num(budget, 2)} 元时最多能买到 ${nutri(best.protein)} g 蛋白质（${best.items.join(" + ")}）。`
    : `这家店这一餐，在价格 ≤ ${num(budget, 2)} 元时买不到任何有官方营养的主食。`;
}

// 更多选项摘要：把当前设置压缩成一行
function updateOptionsSummary() {
  const el = $("options-summary");
  if (!el) return;
  const tol = Number($("s_tolerance").value || 0);
  const mains = $("s_max_mains").value;
  const fat = $("s_fat").value.trim();
  const kmax = $("s_kcal_max").value.trim();
  const kmin = $("s_kcal_min").value.trim();
  const side = $("s_side").checked;
  const parts = [
    `允许少 ${tol} g`,
    `主食最多 ${mains} 个`,
    fat ? `脂肪 ≤ ${fat} g` : "不限脂肪",
  ];
  if (kmax) parts.push(`热量 ≤ ${kmax} kcal`);
  if (kmin) parts.push(`热量 ≥ ${kmin} kcal`);
  if (side) parts.push("可加 1 份配菜");
  el.textContent = `更多选项：${parts.join(" · ")}`;
}

// 餐次分段按钮与隐藏 select 双向同步
function syncMealSeg() {
  const meal = $("s_meal").value;
  document.querySelectorAll("#meal-seg button").forEach((b) => {
    b.setAttribute("aria-pressed", b.dataset.meal === meal ? "true" : "false");
  });
}

// -------------------------------------------------------------------- bindings
$("s_meal").addEventListener("change", () => {
  syncMealSeg();
  invalidateResult();
  loadLandscape();
});
$("s_max_mains").addEventListener("change", () => { invalidateResult(); loadLandscape(); });
$("s_budget").addEventListener("input", () => { invalidateResult(); updateCapHint(); });
$("s_protein").addEventListener("input", () => {
  $("s-calc-note").textContent = "";
  invalidateResult();
});
["s_fat", "s_kcal_min", "s_kcal_max", "s_tolerance"].forEach((id) => {
  $(id).addEventListener("input", () => { invalidateResult(); updateOptionsSummary(); });
});
$("s_side").addEventListener("change", () => { invalidateResult(); updateOptionsSummary(); });
$("s-calc").onclick = singleCalc;
$("s-plan").onclick = () => singlePlan();

document.querySelectorAll("#meal-seg button").forEach((b) => {
  b.addEventListener("click", () => {
    if ($("s_meal").value === b.dataset.meal) return;
    $("s_meal").value = b.dataset.meal;
    $("s_meal").dispatchEvent(new Event("change"));
  });
});

$("rank-toggle").onclick = () => { RANK_EXPANDED = !RANK_EXPANDED; renderRank(); };

$("btn-save").onclick = async () => {
  try {
    await api("/api/settings", {
      date: $("date").value || null,
      store: { code: $("store_code").value },
      meal_times: { breakfast: $("t_breakfast").value, lunch: $("t_lunch").value, dinner: $("t_dinner").value },
    });
    await boot();
    await loadLandscape();
    setStatus("门店/日期/时段已保存。旧方案与旧试算价已失效，请重新计算。");
  } catch (e) { setStatus("保存失败：" + e.message); }
};

$("btn-refresh").onclick = async () => {
  setStatus("正在按当前门店/日期/时段拉取菜单…");
  try {
    const r = await api("/api/refresh", {});
    await boot();
    await loadLandscape();
    setStatus(`刷新完成：${r.date} · 来源 ${r.source} · ${r.fetched_at}。`);
  } catch (e) { setStatus("刷新失败：" + e.message); }
};

let resizeTimer = null;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => drawChart(), 150);
});

document.addEventListener("click", (ev) => {
  if (!ev.target.closest("#chart-panel")) $("chart-tip").hidden = true;
});

async function init() {
  try {
    await boot();
    await loadLandscape();
    syncMealSeg();
    updateOptionsSummary();
    await singlePlan(null, { coupon: false, auto: true });
  } catch (e) { setStatus("初始化失败：" + e.message); }
}

init();
