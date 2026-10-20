const $ = (id) => document.getElementById(id);
let BOOT = null;
let LAND = null;           // landscape payload for the current meal
let S = null;              // current result payload
let SSIG = "";             // signature of the current plan
let SREMOVED = {};         // { mainCode: [optionCode, ...] }
let SLAST = null;          // last valid payload
let WARN = {};             // { meal: "该时段返回的是另一餐次的菜单" }
let RANK_EXPANDED = false;

const STATUS_LABEL = {
  official: ["官方", "official"],
  official_alias: ["官方·别名", "alias"],
  ambiguous_spec: ["规格不唯一", "unknown"],
  not_found: ["无营养数据", "unknown"],
  duplicate_conflict: ["同名冲突", "unknown"],
};

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
const isNarrow = () => window.matchMedia("(max-width: 600px)").matches;
const isOffline = () => (BOOT && BOOT.source) === "offline";

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
  const mealLabel = { breakfast: "早餐", lunch: "午餐", dinner: "晚餐" }[meal] || meal;
  const mealTime = (s.meal_times && s.meal_times[meal]) || (c.meal_times && c.meal_times[meal]) || "—";

  let kind = "cache";
  let text = "";
  if (source === "offline") {
    kind = "offline";
    text = "离线样例数据，价格不是门店实时价";
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
      <td>${idx + 1}</td>
      <td class="name">${it.name}</td>
      <td>${num(it.price, 2)} 元</td>
      <td>${num(it.protein)} g</td>
      <td>${num(it.fat)} g</td>
      <td>${num(it.kcal, 0)} kcal</td>
      <td>${num(it.protein_per_yuan)} g/元</td>
    </tr>`).join("");
  $("rank-table").innerHTML =
    `<tr><th>#</th><th class="name">主食</th><th>价格</th><th>蛋白质</th><th>脂肪</th><th>热量</th><th>每元蛋白质</th></tr>${rows}`;

  $("rank-cards").innerHTML = shown.map((it) => `
    <div class="rank-card ${planCodes.has(String(it.code)) ? "in-plan" : ""}">
      <div class="rc-name">${it.name}</div>
      <div class="rc-meta">${num(it.protein)} g · ${num(it.price, 2)} 元 · ${num(it.protein_per_yuan)} g/元</div>
    </div>`).join("");

  const toggle = $("rank-toggle");
  toggle.hidden = items.length <= 5;
  toggle.textContent = RANK_EXPANDED ? "收起" : "展开全部（前 10）";
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
const PAD = { l: 56, r: 18, t: 24, b: 46 };
// a taller canvas on phones keeps the plot >= 240 px at 390 px wide
const chartHeight = () => (isNarrow() ? 500 : 420);

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
  const target = Number($("s_protein").value);
  const budget = Number($("s_budget").value);
  const rec = planPoint(S);
  const saver = saverPoint(S);

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
  g += `<text class="axis-label" x="${(PAD.l + CHART_W - PAD.r) / 2}" y="${H - 8}" text-anchor="middle">价格（元）</text>`;
  g += `<text class="axis-label" x="14" y="${(PAD.t + H - PAD.b) / 2}" text-anchor="middle" transform="rotate(-90 14 ${(PAD.t + H - PAD.b) / 2})">蛋白质（g）</text>`;

  if (Number.isFinite(budget) && budget > 0 && Number.isFinite(target) && target > 0) {
    const x2 = Math.max(PAD.l + 1, x(budget));
    const y1 = Math.min(H - PAD.b - 1, y(target));
    g += `<rect class="region" data-w="${(x2 - PAD.l).toFixed(1)}" data-h="${(y1 - PAD.t).toFixed(1)}" x="${PAD.l}" y="${PAD.t}" width="${(x2 - PAD.l).toFixed(1)}" height="${(y1 - PAD.t).toFixed(1)}"/>`;
    g += `<text class="region-label" x="${PAD.l + 6}" y="${PAD.t + 14}">达标区域</text>`;
  }

  items.forEach((d, idx) => {
    const hot = idx < 3;
    g += `<circle class="dot ${hot ? "hot" : ""}" data-point="${idx}" cx="${x(d.price).toFixed(1)}" cy="${y(d.protein).toFixed(1)}" r="${hot ? 5.5 : 4.5}"/>`;
  });

  const points = items.slice();
  if (saver) {
    points.push(saver);
    const si = points.length - 1;
    g += `<circle class="saver-marker" data-point="${si}" data-x="${x(saver.price).toFixed(1)}" data-y="${y(saver.protein).toFixed(1)}" cx="${x(saver.price).toFixed(1)}" cy="${y(saver.protein).toFixed(1)}" r="8"/>`;
  }
  if (rec) {
    points.push(rec);
    const ri = points.length - 1;
    g += `<path class="rec-marker" data-point="${ri}" data-x="${x(rec.price).toFixed(1)}" data-y="${y(rec.protein).toFixed(1)}" d="${starPath(x(rec.price), y(rec.protein), 11, 4.6)}"/>`;
  }

  const labels = items.slice(0, 3).map((d, idx) => ({ idx, cx: x(d.price), cy: y(d.protein), name: d.name }));
  labels.forEach((l) => {
    g += `<text class="dot-label" data-label="${l.idx}" x="${(l.cx + 9).toFixed(1)}" y="${(l.cy - 9).toFixed(1)}" text-anchor="start">${l.name}</text>`;
  });

  svg.innerHTML = g;
  if (!isNarrow()) layoutLabels(svg, labels);
  bindTips(svg, points);
}

function boxesOverlap(a, b, margin = 3) {
  return !(a.r + margin < b.l || a.l - margin > b.r || a.b + margin < b.t || a.t - margin > b.b);
}

function layoutLabels(svg, labels) {
  const H = chartHeight();
  const placed = [];
  svg.querySelectorAll("circle.dot, .rec-marker, .saver-marker").forEach((el) => {
    const b = el.getBBox();
    placed.push({ l: b.x, r: b.x + b.width, t: b.y, b: b.y + b.height });
  });
  const candidates = [
    { dx: 9, dy: -9, anchor: "start" },
    { dx: -9, dy: -9, anchor: "end" },
    { dx: 9, dy: 16, anchor: "start" },
    { dx: -9, dy: 16, anchor: "end" },
    { dx: 0, dy: -18, anchor: "middle" },
    { dx: 0, dy: 26, anchor: "middle" },
    { dx: 14, dy: -32, anchor: "start" },
    { dx: -14, dy: -32, anchor: "end" },
    { dx: 14, dy: 38, anchor: "start" },
    { dx: -14, dy: 38, anchor: "end" },
    { dx: 0, dy: -40, anchor: "middle" },
    { dx: 0, dy: 46, anchor: "middle" },
  ];
  labels.forEach((entry) => {
    const text = svg.querySelector(`text.dot-label[data-label="${entry.idx}"]`);
    if (!text) return;
    for (const c of candidates) {
      text.setAttribute("x", (entry.cx + c.dx).toFixed(1));
      text.setAttribute("y", (entry.cy + c.dy).toFixed(1));
      text.setAttribute("text-anchor", c.anchor);
      const b = text.getBBox();
      const box = { l: b.x, r: b.x + b.width, t: b.y, b: b.y + b.height };
      const outside = box.l < PAD.l - 2 || box.r > CHART_W - PAD.r + 2
        || box.t < 2 || box.b > H - PAD.b - 2;
      if (outside || placed.some((p) => boxesOverlap(p, box))) continue;
      placed.push(box);
      const moved = Math.abs(c.dx) > 10 || c.dy > 10 || c.dy < -12;
      if (moved) {
        const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
        line.setAttribute("class", "leader");
        line.setAttribute("x1", entry.cx.toFixed(1));
        line.setAttribute("y1", entry.cy.toFixed(1));
        line.setAttribute("x2", (entry.cx + c.dx * 0.75).toFixed(1));
        line.setAttribute("y2", (entry.cy + c.dy * 0.75).toFixed(1));
        svg.insertBefore(line, text);
      }
      return;
    }
    text.setAttribute("opacity", "0");
  });
}

function bindTips(svg, points) {
  const tip = $("chart-tip");
  const show = (ev, idx) => {
    const row = points[idx];
    if (!row) return;
    tip.hidden = false;
    tip.innerHTML = `<b>${row.name}</b><br>价格 ${num(row.price, 2)} 元 · 蛋白质 ${num(row.protein)} g<br>
      脂肪 ${num(row.fat)} g · 热量 ${num(row.kcal, 0)} kcal<br>每元蛋白质 ${num(row.ppy)} g`;
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

function relaxLines(s) {
  const f = s.frontier || {};
  const tg = s.targets || {};
  const out = [];
  if (f.reach_price) {
    out.push(`只放宽价格：达到 ${num(tg.protein_min, 0)} g 蛋白质最低需 <b>${num(f.reach_price.cost, 2)} 元</b>（${comboName(f.reach_price)}）。`);
  } else {
    out.push(`只放宽价格，仍达不到 ${num(tg.protein_min, 0)} g 蛋白质。`);
  }
  if (tg.fat_max !== null && tg.fat_max !== undefined) {
    out.push(f.relax_fat
      ? `只放宽脂肪：需要放宽到 <b>${num(f.relax_fat.fat)} g</b>（${comboName(f.relax_fat)}，${num(f.relax_fat.cost, 2)} 元）。`
      : "只放宽脂肪，仍达不到蛋白质目标。");
  }
  if (tg.kcal_max !== null && tg.kcal_max !== undefined) {
    out.push(f.relax_kcal
      ? `只放宽热量上限：需要放宽到 <b>${num(f.relax_kcal.kcal, 0)} kcal</b>（${comboName(f.relax_kcal)}）。`
      : "只放宽热量上限，仍达不到蛋白质目标。");
  } else if (tg.kcal_min !== null && tg.kcal_min !== undefined) {
    out.push(f.relax_kcal
      ? `只放宽热量下限：需要放宽到 <b>${num(f.relax_kcal.kcal, 0)} kcal</b>（${comboName(f.relax_kcal)}）。`
      : "只放宽热量下限，仍达不到蛋白质目标。");
  }
  return out;
}

function hintLines(s) {
  const status = s.protein_status || (s.feasible ? "met" : "under");
  if (status === "near") {
    return [`接近达标：比目标少 ${num(s.protein_gap)} g，在你允许的 ${num(s.protein_tolerance, 0)} g 内。`];
  }
  if (status !== "under") return [];
  const lines = ["当前条件下没有满足全部要求的方案"];
  const cap = (s.frontier || {}).max_protein;
  if (!(s.items || []).length) {
    lines.push("没有任何组合同时满足价格、脂肪与热量上限，因此不显示商品卡片。");
  } else if (cap) {
    lines.push(`预算内最多：${num(cap.protein)} g 蛋白质，还差 ${num(cap.protein_gap)} g（${comboName(cap)}，${num(cap.cost, 2)} 元）。`);
  }
  const relax = relaxLines(s);
  if (relax.length) lines.push(relax.join("；"));
  return lines.slice(0, 3);
}

function statusBadge(s) {
  const status = s.protein_status || (s.feasible ? "met" : "under");
  if (status === "met") return '<span class="badge official">达标</span>';
  if (status === "near") return `<span class="badge near">接近达标（少 ${num(s.protein_gap)} g）</span>`;
  return '<span class="badge cap">预算内最多</span>';
}

function renderSingle(s) {
  if (!s) { $("s-result-panel").hidden = true; return; }
  const items = s.items || [];
  const t = s.totals || {};
  const q = s.quotes && s.quotes[s.meal];
  const main = items.find((i) => i.kind === "main");
  const quoted = q && q.status === "ok";
  const hasItems = items.length > 0;
  const price = quoted ? Number(q.amount) : Number(s.cost || 0);
  const priceText = hasItems ? num(price, 2) : "—";
  const priceLabel = hasItems ? (quoted ? "已试算" : "参考价") : "未选到商品";
  const status = s.protein_status || (s.feasible ? "met" : "under");
  const lines = hintLines(s);

  const itemRows = items.map((i) => `
    <div class="item">
      <div class="item-line">
        <span class="name">${i.name}</span>
        <span class="qty">× ${i.quantity}</span>
        <span class="unit">${num(i.price_ref, 2)} 元/份</span>
      </div>
      <details class="item-detail">
        <summary>明细</summary>
        <div class="sub">${num(i.nutrition?.energyKcal, 0)} kcal · 蛋白质 ${num(i.nutrition?.protein)} g ·
          脂肪 ${num(i.nutrition?.fat)} g · 碳水 ${num(i.nutrition?.carbohydrate)} g ·
          钠 ${num(i.nutrition?.sodium, 0)} mg</div>
        ${(i.removal_notes || []).map((n) => `<div class="strict-note">${n}</div>`).join("")}
        ${i.modification && i.modification.length
          ? `<div class="sub">点单参数：${i.modification.map((m) => `${m.code}:${m.key}`).join("，")}</div>` : ""}
      </details>
    </div>`).join("");

  const mainCount = items.filter((i) => i.kind === "main").length;
  const suggestions = (s.suggestions || []).map((o) => {
    const target = String(o.target_code || main?.code || "");
    const checked = (SREMOVED[target] || []).includes(String(o.code));
    return `<label class="sugg">
      <input type="checkbox" class="srem" data-code="${o.code}" data-target="${target}" ${checked ? "checked" : ""}>
      <span>去掉 ${o.name}${mainCount > 1 ? `（${o.target_name || target}）` : ""}
        ${o.muscle_warning ? `<span class="muscle-warn">${o.muscle_note}</span>` : ""}
      </span>
    </label>`;
  }).join("");

  const upgradeList = (s.upgrades && s.upgrades.length) ? s.upgrades : (s.upgrade ? [s.upgrade] : []);
  const upgrade = upgradeList.map((u) => {
    const over = u.fits === false;
    return `<div class="upgrade ${over ? "over" : ""}">
      <div class="up-head"><b>升级一档：${u.label}</b>
        ${u.fit_note ? `<span class="fit ${over ? "over" : "ok"}">${u.fit_note}</span>` : ""}</div>
      <div>+${num(u.delta_protein)} g 蛋白质 · +${num(u.delta_price, 2)} 元 · +${num(u.delta_fat)} g 脂肪</div>
      ${u.show_conversion ? `<div class="sub">每多 1 元，多 ${num(u.protein_per_extra_yuan)} g 蛋白质。</div>` : ""}
      <div class="sub">依据：${u.evidence || "两款均为官方整款数据"}</div>
    </div>`;
  }).join("");

  const saver = s.saver;
  const saverRow = (saver && status === "met") ? `<div class="saver-row">
      <span>省钱选项：<b>${comboName(saver)}</b>，少 ${num(saver.protein_less)} g 蛋白质，省 ${num(saver.money_saved, 2)} 元</span>
      <button class="ghost tiny" id="s-saver">换成这个</button>
    </div>` : "";

  const hint = lines.length ? `<div class="violations ${status === "near" ? "near" : ""}">
      ${lines.map((line, idx) => `<div class="v ${idx === 0 ? "title" : ""} ${idx === 2 ? "relax" : ""}">${line}</div>`).join("")}
    </div>` : "";

  const totalsLine = `每元蛋白质 ${num(s.protein_per_yuan)} g · 热量 ${num(t.energyKcal, 0)} kcal ·
    脂肪 ${num(t.fat)} g · 碳水 ${num(t.carbohydrate)} g`;

  $("s-result").innerHTML = `
    <div class="single-card">
      <div class="single-head">
        <div class="metrics">
          <div class="metric"><span class="metric-num">${num(t.protein)}</span><span class="metric-unit">g 蛋白质</span></div>
          <div class="metric price"><span class="metric-num">${priceText}</span><span class="metric-unit">元</span>
            <span class="price-tag">${priceLabel}</span></div>
        </div>
        <div class="statuses"><span class="badge official">官方标准版</span>${statusBadge(s)}</div>
      </div>
      <div class="result-sub">${totalsLine}</div>
      ${hint}
      ${itemRows}
      ${saverRow}
      <div class="quote-row">
        <button id="s-quote" class="ghost" title="只按当前这一餐的商品/定制试算，不会重新选餐">为当前方案报价${isOffline() ? "（离线桩报价）" : ""}</button>
        <span class="sub">${singleQuoteLine(q)}</span>
      </div>
      ${suggestions ? `<details class="sugg-box"><summary>点单建议（点击展开，默认不改动）</summary>${suggestions}
        <div class="sub">去掉配料只写进点单参数；营养数值仍为标准版官方数据。</div></details>` : ""}
      ${upgrade ? `<details class="upgrade-box"><summary>肉量升级（整款替换）</summary>${upgrade}</details>` : ""}
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

  renderRank();
  drawChart();
}

// ---------------------------------------------------------------------- flows
async function singlePlan(extra) {
  const body = singleRequest();
  const bad = singleValidate(body);
  if (bad) { setSStatus(bad); return; }
  setSStatus("正在枚举这一餐的全部候选…");
  try {
    const out = await api("/api/meal", { ...body, ...(extra || {}), quote: false });
    S = out; SLAST = out; SSIG = out.signature || "";
    SREMOVED = {};
    renderSingle(S);
    const gap = Number(out.protein_gap || 0);
    const tol = Number(out.protein_tolerance || 0);
    const head = out.context_warning ? `${out.context_warning} ` : "";
    let msg;
    if (out.protein_status === "near") {
      msg = `接近达标：比目标少 ${num(gap)} g，在你允许的 ${num(tol, 0)} g 内（参考价 ${num(out.cost, 2)} 元）。`;
    } else if (out.protein_status === "under") {
      msg = "当前条件下无解；主卡片只显示预算内蛋白质最高的组合，放宽方案见下方。";
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
          meal: S.meal, meal_label: S.meal_label };
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
          meal: S.meal, meal_label: S.meal_label };
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
    ? `这家店这一餐，在价格 ≤ ${num(budget, 2)} 元时最多能买到 ${num(best.protein)} g 蛋白质（${best.items.join(" + ")}）。`
    : `这家店这一餐，在价格 ≤ ${num(budget, 2)} 元时买不到任何有官方营养的主食。`;
}

// -------------------------------------------------------------------- bindings
$("s_meal").addEventListener("change", () => { invalidateResult(); loadLandscape(); });
$("s_max_mains").addEventListener("change", () => { invalidateResult(); loadLandscape(); });
$("s_budget").addEventListener("input", () => { invalidateResult(); updateCapHint(); });
$("s_protein").addEventListener("input", () => {
  $("s-calc-note").textContent = "";
  invalidateResult();
});
["s_fat", "s_kcal_min", "s_kcal_max", "s_tolerance"].forEach((id) => {
  $(id).addEventListener("input", () => invalidateResult());
});
$("s_side").addEventListener("change", () => invalidateResult());
$("s-calc").onclick = singleCalc;
$("s-plan").onclick = () => singlePlan();

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
  } catch (e) { setStatus("初始化失败：" + e.message); }
}

init();
