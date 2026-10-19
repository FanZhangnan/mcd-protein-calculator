const $ = (id) => document.getElementById(id);
let BOOT = null;
let LAND = null;           // landscape payload for the current meal
let S = null;              // current result payload
let SSIG = "";             // signature of the current plan
let SREMOVED = {};         // { mainCode: [optionCode, ...] }
let SLAST = null;          // last valid payload
let WARN = {};             // { meal: "该时段返回的是另一餐次的菜单" }

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
const num = (v, d = 1) => (v === null || v === undefined ? "—" : Number(v).toFixed(d));

function badge(status) {
  const [label, cls] = STATUS_LABEL[status] || [status || "未知", "unknown"];
  return `<span class="badge ${cls}">${label}</span>`;
}

// ---------------------------------------------------------------- bootstrap
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
  const srcLabel = { live: "实时", cache: "缓存", offline: "离线", none: "未加载", unavailable: "不可用" }[c.source] || c.source;
  $("meta").innerHTML =
    `<span class="pill">${srcLabel}</span> 数据时间 ${c.fetched_at || "—"} · 门店 ${c.store_code || "—"}` +
    ` · 时段 早${c.meal_times?.breakfast || "—"} / 午${c.meal_times?.lunch || "—"} / 晚${c.meal_times?.dinner || "—"}` +
    ` · 营养条目 ${BOOT.catalog.rows || 0}/${BOOT.catalog.unique_names || 0}` +
    (c.error ? ` · <b class="err">${c.error}</b>` : "");
  renderUnknown();
}

function renderUnknown() {
  const meal = $("s_meal").value;
  const unk = (BOOT && BOOT.unknown || {})[meal] || [];
  const rows = unk.map((u) => `<tr><td>${u.name}</td><td>${u.code}</td><td>${u.status}</td><td>${u.note || ""}</td></tr>`);
  $("unknown").innerHTML = rows.length
    ? `<div>本餐次以下 SKU 无法唯一匹配官方营养，<b>未纳入计算</b>（不会按 0 处理）：</div>
       <table><tr><th>名称</th><th>code</th><th>状态</th><th>原因</th></tr>${rows.join("")}</table>`
    : "本餐次参与计算的所有 SKU 均有可核实的官方营养来源。";
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
    $("chart").innerHTML = `<text x="340" y="160" text-anchor="middle" class="tick">性价比图加载失败：${e.message}</text>`;
    return;
  }
  renderRankTable();
  drawChart();
  updateCapHint();
  renderUnknown();
  if (WARN[meal] && !S) setSStatus(WARN[meal]);
}

function renderRankTable() {
  if (!LAND || !LAND.items.length) {
    $("rank-table").innerHTML = "<tr><td class='sub'>本餐次没有匹配到官方营养的主食。</td></tr>";
    return;
  }
  const rows = LAND.items.slice(0, 10).map((it, idx) => `
    <tr class="${idx < 3 ? "hot" : ""}">
      <td>${idx + 1}</td>
      <td class="name">${it.name}</td>
      <td>${num(it.price, 2)} 元</td>
      <td>${num(it.protein)} g</td>
      <td>${num(it.fat)} g</td>
      <td>${num(it.kcal, 0)} kcal</td>
      <td>${num(it.protein_per_yuan)} g/元</td>
    </tr>`).join("");
  $("rank-table").innerHTML =
    `<tr><th>#</th><th>主食</th><th>价格</th><th>蛋白质</th><th>脂肪</th><th>热量</th><th>每元蛋白质</th></tr>${rows}`;
}

const fmtTick = (v) => (Number.isInteger(v) ? String(v) : Number(v).toFixed(1));

const niceProteinMax = (v) => (v <= 10 ? 8 : Math.ceil(v / 20) * 20);

const niceMax = (value) => {
  if (!Number.isFinite(value) || value <= 0) return 10;
  const step = value > 80 ? 20 : value > 40 ? 10 : 5;
  return Math.ceil((value * 1.15) / step) * step;
};

function drawChart() {
  const svg = $("chart");
  if (!LAND || !LAND.items.length) {
    svg.innerHTML = `<text x="340" y="160" text-anchor="middle" class="tick">本餐次暂无可绘制的主食</text>`;
    return;
  }
  const W = 680, H = 330, pad = { l: 56, r: 20, t: 18, b: 46 };
  const maxPrice = niceMax(Math.max(...LAND.items.map((d) => d.price)));
  const proteinTarget = Number($("s_protein").value);
  const maxProtein = niceProteinMax(Math.max(
    ...LAND.items.map((d) => d.protein),
    Number.isFinite(proteinTarget) && proteinTarget > 0 ? proteinTarget : 0,
  ));
  const x = (v) => pad.l + (v / maxPrice) * (W - pad.l - pad.r);
  const y = (v) => H - pad.b - (v / maxProtein) * (H - pad.t - pad.b);

  const budget = Number($("s_budget").value);
  const protein = Number($("s_protein").value);
  const hasTarget = Number.isFinite(budget) && budget > 0 && Number.isFinite(protein) && protein >= 0;

  let g = "";
  for (let i = 0; i <= 4; i += 1) {
    const px = pad.l + (i / 4) * (W - pad.l - pad.r);
    const py = H - pad.b - (i / 4) * (H - pad.t - pad.b);
    g += `<line class="grid" x1="${px}" y1="${pad.t}" x2="${px}" y2="${H - pad.b}"/>`;
    g += `<line class="grid" x1="${pad.l}" y1="${py}" x2="${W - pad.r}" y2="${py}"/>`;
    g += `<text class="tick" x="${px}" y="${H - pad.b + 15}" text-anchor="middle">${fmtTick((maxPrice * i) / 4)}</text>`;
    g += `<text class="tick" x="${pad.l - 8}" y="${py + 3}" text-anchor="end">${fmtTick((maxProtein * i) / 4)}</text>`;
  }
  g += `<line class="axis" x1="${pad.l}" y1="${H - pad.b}" x2="${W - pad.r}" y2="${H - pad.b}"/>`;
  g += `<line class="axis" x1="${pad.l}" y1="${pad.t}" x2="${pad.l}" y2="${H - pad.b}"/>`;
  g += `<text class="axis-label" x="${(pad.l + W - pad.r) / 2}" y="${H - 10}" text-anchor="middle">价格（元）</text>`;
  g += `<text class="axis-label" x="14" y="${(pad.t + H - pad.b) / 2}" text-anchor="middle" transform="rotate(-90 14 ${(pad.t + H - pad.b) / 2})">蛋白质（g）</text>`;

  if (hasTarget) {
    const x1 = x(Math.min(budget, maxPrice));
    const y1 = y(Math.min(protein, maxProtein));
    g += `<rect class="region" x="${pad.l}" y="${pad.t}" width="${Math.max(0, x1 - pad.l)}" height="${Math.max(0, y1 - pad.t)}"/>`;
    g += `<text class="region-label" x="${pad.l + 6}" y="${pad.t + 14}">达标区域</text>`;
  }

  const hotRows = LAND.items.slice(0, 3);
  const hot = hotRows.map((d) => d.code);
  const placed = [];
  const labels = [];
  const leaders = [];
  hotRows.forEach((d) => {
    const width = d.name.length * 11 + 8;
    const anchor = x(d.price) > W - 170 ? "end" : "start";
    const dx = anchor === "end" ? -9 : 9;
    const lx = x(d.price) + dx;
    let ly = y(d.protein) - 9;
    const box = () => ({
      l: anchor === "end" ? lx - width : lx, r: anchor === "end" ? lx : lx + width,
      t: ly - 11, b: ly + 3,
    });
    let tries = 0;
    while (placed.some((p) => !(box().r < p.l || box().l > p.r || box().b < p.t || box().t > p.b))
           && tries < 8) {
      ly -= 15;
      tries += 1;
    }
    if (ly < pad.t + 12) ly = y(d.protein) + 20;
    placed.push(box());
    if (Math.abs(ly - (y(d.protein) - 9)) > 2) {
      leaders.push(`<line class="leader" x1="${x(d.price)}" y1="${y(d.protein) - 5}"
        x2="${lx}" y2="${ly - 3}"/>`);
    }
    labels.push(`<text class="dot-label" x="${lx}" y="${ly}" text-anchor="${anchor}">${d.name}</text>`);
  });
  LAND.items.forEach((d) => {
    const isHot = hot.includes(d.code);
    g += `<circle class="dot ${isHot ? "hot" : ""}" cx="${x(d.price)}" cy="${y(d.protein)}" r="${isHot ? 6 : 4.5}" data-code="${d.code}"/>`;
  });
  g += leaders.join("") + labels.join("");
  svg.innerHTML = g;

  const tip = $("chart-tip");
  svg.querySelectorAll("circle.dot").forEach((el) => {
    const row = LAND.items.find((d) => String(d.code) === el.dataset.code);
    if (!row) return;
    el.addEventListener("mouseenter", () => {
      tip.hidden = false;
      tip.innerHTML = `<b>${row.name}</b><br>价格 ${num(row.price, 2)} 元 · 蛋白质 ${num(row.protein)} g<br>
        脂肪 ${num(row.fat)} g · 热量 ${num(row.kcal, 0)} kcal<br>每元蛋白质 ${num(row.protein_per_yuan)} g`;
    });
    el.addEventListener("mousemove", (ev) => {
      const rect = svg.getBoundingClientRect();
      tip.style.left = `${ev.clientX - rect.left + 12}px`;
      tip.style.top = `${ev.clientY - rect.top + 12}px`;
    });
    el.addEventListener("mouseleave", () => { tip.hidden = true; });
  });
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

function invalidateResult(message) {
  S = null; SLAST = null; SSIG = ""; SREMOVED = {};
  $("s-result-panel").hidden = true;
  setSStatus(message || "参数已变化，请重新算这一餐。");
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

// --------------------------------------------------------------------- inputs
function singleRequest() {
  const body = {
    meal: $("s_meal").value,
    protein_min: Number($("s_protein").value),
    budget: Number($("s_budget").value),
    max_mains: Number($("s_max_mains").value),
    allow_side: $("s_side").checked,
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

function comboName(o) {
  if (!o) return "";
  if (Array.isArray(o.items) && o.items.length) return o.items.join(" + ");
  return o.name || "";
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

function renderSingle(s) {
  if (!s) { $("s-result-panel").hidden = true; return; }
  const items = s.items || [];
  const t = s.totals || {};
  const q = s.quotes && s.quotes[s.meal];
  const main = items.find((i) => i.kind === "main");
  const money = !items.length
    ? "—（未选到商品）"
    : (q && q.status === "ok" ? `试算价 ${q.amount} 元` : `参考价 ${num(s.cost, 2)} 元`);
  const fallback = !s.feasible && items.length > 0;
  const noCard = !s.feasible && items.length === 0;

  const itemRows = items.map((i) => `
    <div class="item">
      <div class="name">${i.name} × ${i.quantity} ${i.kind === "main" ? '<span class="badge official">主食</span>' : ""}</div>
      <div class="sub">${num(i.nutrition?.energyKcal, 0)} kcal · P ${num(i.nutrition?.protein)} g ·
        F ${num(i.nutrition?.fat)} g · C ${num(i.nutrition?.carbohydrate)} g
        · ${num(i.price_ref, 2)} 元/份 · 行计 ${num(i.line_total_ref, 2)} 元</div>
      ${(i.removal_notes || []).map((n) => `<div class="strict-note">${n}</div>`).join("")}
      ${i.modification && i.modification.length
        ? `<div class="sub">点单参数：${i.modification.map((m) => `${m.code}:${m.key}`).join("，")}</div>` : ""}
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
  const upgrade = upgradeList.map((u) => `<div class="upgrade">
      <b>升级一档：${u.label}</b>
      <div>+${num(u.delta_protein)} g 蛋白质 · +${num(u.delta_price, 2)} 元 · +${num(u.delta_fat)} g 脂肪</div>
      ${u.show_conversion ? `<div class="sub">每多 1 元，多 ${num(u.protein_per_extra_yuan)} g 蛋白质。</div>` : ""}
      <div class="sub">依据：${u.evidence || "两款均为官方整款数据"}</div>
    </div>`).join("");

  let noSolution = "";
  if (!s.feasible) {
    const cap = (s.frontier || {}).max_protein;
    const gap = cap && cap.protein_gap !== null && cap.protein_gap !== undefined
      ? `，还差 ${num(cap.protein_gap)} g` : "";
    const lines = relaxLines(s).map((line) => `<div class="relax">${line}</div>`).join("");
    noSolution = `<div class="violations">
      <div class="v title">当前条件下没有满足全部要求的方案</div>
      ${noCard ? `<div class="v">没有任何组合同时满足价格、脂肪与热量上限，因此不显示商品卡片。</div>`
        : `<div class="v">预算内最多：${cap ? `${num(cap.protein)} g 蛋白质${gap}（${comboName(cap)}，${num(cap.cost, 2)} 元）` : "—"}</div>`}
      ${lines}
    </div>`;
  }

  const totalsLine = `热量 ${num(t.energyKcal, 0)} kcal · 蛋白质 ${num(t.protein)} g ·
    脂肪 ${num(t.fat)} g · 碳水 ${num(t.carbohydrate)} g · 钠 ${num(t.sodium, 0)} mg（官方）`;
  const ppy = s.protein_per_yuan;

  $("s-result").innerHTML = `
    <div class="single-card">
      <div class="single-head">
        <div>
          <b>${s.meal_label || s.meal}</b>
          <span class="badge official">官方标准版</span>
          ${s.feasible ? '<span class="badge official">达标</span>' : ""}
          ${fallback ? '<span class="badge cap">预算内最多</span>' : ""}
        </div>
        <div><b>${money}</b></div>
      </div>
      <div class="sub">${singleQuoteLine(q)}</div>
      ${itemRows}
      <div class="nutr"><span>${totalsLine}</span>
        <span><b>每元蛋白质 ${num(ppy)} g</b></span></div>
      ${noSolution}
      ${suggestions ? `<details class="sugg-box"><summary>点单建议（点击展开；默认不改动）</summary>${suggestions}
        <div class="sub">去掉配料只写进点单参数；营养数值仍为标准版官方数据。</div></details>` : ""}
      ${upgrade}
      <div class="sub">严格模式：营养一律取官方整款标准版；去除配料只影响点单参数，不扣减任何数值。</div>
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
}

// ---------------------------------------------------------------------- flows
async function singlePlan() {
  const body = singleRequest();
  const bad = singleValidate(body);
  if (bad) { setSStatus(bad); return; }
  setSStatus("正在枚举这一餐的全部候选…");
  try {
    const out = await api("/api/meal", { ...body, quote: false });
    S = out; SLAST = out; SSIG = out.signature || "";
    SREMOVED = {};
    renderSingle(S);
    setSStatus((out.context_warning ? `${out.context_warning} ` : "")
      + (out.feasible
        ? `已找到满足全部硬约束的方案（参考价 ${num(out.cost, 2)} 元）。`
        : "当前条件下无解；主卡片只显示预算内蛋白质最高的组合，放宽方案见下方。"));
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

// -------------------------------------------------------------------- bindings
$("s_meal").addEventListener("change", () => { invalidateResult(); loadLandscape(); });
$("s_max_mains").addEventListener("change", () => { invalidateResult(); loadLandscape(); });
$("s_budget").addEventListener("input", () => { invalidateResult(); updateCapHint(); drawChart(); });
$("s_protein").addEventListener("input", () => {
  // the body-data note describes a prefill that no longer applies
  $("s-calc-note").textContent = "";
  invalidateResult();
  drawChart();
});
["s_fat", "s_kcal_min", "s_kcal_max"].forEach((id) => {
  $(id).addEventListener("input", () => invalidateResult());
});
$("s_side").addEventListener("change", () => invalidateResult());
$("s-calc").onclick = singleCalc;
$("s-plan").onclick = singlePlan;
$("s-quote").onclick = singleQuote;

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

async function init() {
  try {
    await boot();
    await loadLandscape();
  } catch (e) { setStatus("初始化失败：" + e.message); }
}

init();
