const $ = (id) => document.getElementById(id);
let BOOT = null;
let S = null;              // current single-meal payload
let SSIG = "";             // signature of the current meal
let SREMOVED = {};         // { mainCode: [optionCode, ...] }
let SLAST = null;          // last valid payload, used to restore after a 400

const STATUS_LABEL = {
  official: ["官方", "official"],
  official_alias: ["官方·别名", "alias"],
  ambiguous_spec: ["规格不唯一", "unknown"],
  not_found: ["无营养数据", "unknown"],
  duplicate_conflict: ["同名冲突", "unknown"],
  recorded: ["历史快照", "official"],
  synthetic: ["测试数据", "unknown"],
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
    err.supported = data.supported;
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

async function boot() {
  BOOT = await api("/api/bootstrap", undefined, "GET");
  const s = BOOT.state;
  $("date").value = s.date || "";
  $("store_code").value = s.store?.code || "";
  $("t_breakfast").value = s.meal_times?.breakfast || "08:15";
  $("t_lunch").value = s.meal_times?.lunch || "12:15";
  $("t_dinner").value = s.meal_times?.dinner || "18:30";

  const c = BOOT.cache || {};
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
  const unk = (BOOT.unknown || {})[meal] || [];
  const rows = unk.map((u) => `<tr><td>${u.name}</td><td>${u.code}</td><td>${u.status}</td><td>${u.note || ""}</td></tr>`);
  $("unknown").innerHTML = rows.length
    ? `<div>本餐次以下 SKU 无法唯一匹配官方营养，<b>未纳入计算</b>（不会按 0 处理）：</div>
       <table><tr><th>名称</th><th>code</th><th>状态</th><th>原因</th></tr>${rows.join("")}</table>`
    : "本餐次参与计算的所有 SKU 均有可核实的官方营养来源。";
}

const settingsFromForm = () => ({
  date: $("date").value || null,
  store: { code: $("store_code").value },
  meal_times: { breakfast: $("t_breakfast").value, lunch: $("t_lunch").value, dinner: $("t_dinner").value },
});

function singleRequest() {
  const body = {
    meal: $("s_meal").value,
    protein_min: Number($("s_protein").value),
    fat_max: Number($("s_fat").value),
    budget: Number($("s_budget").value),
    allow_side: $("s_side").checked,
  };
  const kmin = $("s_kcal_min").value.trim();
  if (kmin !== "") body.kcal_min = Number(kmin);
  const kmax = $("s_kcal_max").value.trim();
  if (kmax !== "") body.kcal_max = Number(kmax);
  return body;
}

function singleValidate(body) {
  if (!body.meal) return "请选择餐次";
  for (const [key, label] of [["protein_min", "蛋白质下限"], ["fat_max", "脂肪上限"],
                              ["budget", "价格上限"]]) {
    if (!Number.isFinite(body[key])) return `${label}必须是数字`;
    if (body[key] < 0) return `${label}不能是负数`;
  }
  if (body.budget <= 0) return "价格上限必须大于 0";
  for (const key of ["kcal_min", "kcal_max"]) {
    if (body[key] !== undefined && !Number.isFinite(body[key])) return `${key} 必须是数字`;
  }
  if (body.kcal_min !== undefined && body.kcal_max !== undefined
      && body.kcal_max <= body.kcal_min) return "热量上限必须大于下限";
  return "";
}

function singleCalc() {
  const sex = $("s_sex").value;
  const age = Number($("s_age").value);
  const h = Number($("s_height").value);
  const w = Number($("s_weight").value);
  const act = Number($("s_activity").value);
  const n = Math.max(1, Number($("s_meals").value) || 3);
  if (![age, h, w, act].every(Number.isFinite) || age <= 0 || h <= 0 || w <= 0) {
    $("s-calc-note").textContent = "请先填写有效的年龄、身高、体重。";
    return;
  }
  const bmr = 10 * w + 6.25 * h - 5 * age + (sex === "male" ? 5 : -161);
  const tdee = bmr * act;
  const target = tdee + 300;
  const per = target / n;
  const protein = (2.0 * w) / n;
  $("s_kcal_min").value = Math.round(per * 0.9);
  $("s_kcal_max").value = Math.round(per * 1.1);
  $("s_protein").value = Math.round(protein);
  $("s-calc-note").textContent =
    `BMR ${bmr.toFixed(0)} kcal，TDEE ${tdee.toFixed(0)} kcal，增肌参考 ${target.toFixed(0)} kcal/天，`
    + `每餐约 ${per.toFixed(0)} kcal、蛋白质 ${protein.toFixed(1)} g（2.0 g/kg）。热量仅作参考，不作为约束。`;
}

function singleQuoteLine(q) {
  if (!q) return "未试算 · 仅参考价";
  if (q.status === "ok") {
    return `试算价 ${q.amount} 元（查询于 ${q.queried_at || "—"}，取餐时点 ${q.reservation_date || "—"}）`;
  }
  return `试算失败：${q.message || q.status}`;
}

function renderSingle(s) {
  if (!s) { $("s-result-panel").hidden = true; return; }
  const items = s.items || [];
  const t = s.totals || {};
  const q = s.quotes && s.quotes[s.meal];
  const main = items.find((i) => i.kind === "main");
  const money = q && q.status === "ok" ? `试算价 ${q.amount} 元` : `参考价 ${num(s.cost, 2)} 元`;

  const itemRows = items.map((i) => `
    <div class="item">
      <div class="name">${i.name} × ${i.quantity} ${i.kind === "main" ? '<span class="badge official">主食</span>' : ""}</div>
      <div class="sub">${num(i.nutrition?.energyKcal, 0)} kcal · P ${num(i.nutrition?.protein)} g ·
        F ${num(i.nutrition?.fat)} g · C ${num(i.nutrition?.carbohydrate)} g
        · ${num(i.price_ref, 2)} 元</div>
      ${(i.removal_notes || []).map((n) => `<div class="strict-note">${n}</div>`).join("")}
      ${i.modification && i.modification.length
        ? `<div class="sub">点单参数：${i.modification.map((m) => `${m.code}:${m.key}`).join("，")}</div>` : ""}
    </div>`).join("");

  const suggestions = (s.suggestions || []).map((o) => {
    const checked = (SREMOVED[main?.code] || []).includes(String(o.code));
    return `<label class="sugg">
      <input type="checkbox" class="srem" data-code="${o.code}" ${checked ? "checked" : ""}>
      去掉 ${o.name}${o.protein_risk ? "（可能含蛋白质）" : ""}
    </label>`;
  }).join("");

  let upgrade = "";
  if (s.upgrade) {
    const u = s.upgrade;
    upgrade = `<div class="upgrade">
      <b>升级一档：${u.label}</b>
      <div>+${num(u.delta_protein)} g 蛋白质 · +${num(u.delta_fat)} g 脂肪 · +${num(u.delta_price, 2)} 元</div>
      ${u.per_10g_protein ? `<div class="sub">每多补 10 g 蛋白质：多花 ${num(u.per_10g_protein.extra_price, 2)} 元，多 ${num(u.per_10g_protein.extra_fat)} g 脂肪。</div>` : ""}
      <div class="sub">两款均为官方整款数据${u.evidence ? " · " + u.evidence : ""}</div>
    </div>`;
  }

  let fallback = "";
  if (!s.feasible) {
    const f = s.frontier || {};
    const cap = f.max_protein;
    const tg = s.targets || {};
    const lines = [];
    lines.push(`<div class="v">当前条件下没有满足全部要求的方案。${f.message || ""}</div>`);
    if (cap) {
      lines.push(`<div class="v">在脂肪 ≤ ${num(tg.fat_max, 0)} g、价格 ≤ ${num(tg.budget, 2)} 元的条件下，`
        + `这家店这一餐蛋白质最多能达到 <b>${num(cap.protein)} g</b>（${cap.name}，脂肪 ${num(cap.fat)} g，${num(cap.cost, 2)} 元）。</div>`);
    }
    if (f.relax_fat) {
      lines.push(`<div class="v">只放宽脂肪：达到 ${num(tg.protein_min, 0)} g 蛋白质至少需要 `
        + `<b>${num(f.relax_fat.fat)} g 脂肪</b>，对应 ${f.relax_fat.name}（${num(f.relax_fat.cost, 2)} 元）。</div>`);
    }
    if (f.reach_price) {
      lines.push(`<div class="v">只放宽价格：达到 ${num(tg.protein_min, 0)} g 蛋白质最低需 `
        + `<b>${num(f.reach_price.cost, 2)} 元</b>，对应 ${f.reach_price.name}（脂肪 ${num(f.reach_price.fat)} g）。</div>`);
    } else {
      lines.push(`<div class="v">只放宽价格，仍达不到蛋白质目标。</div>`);
    }
    if (tg.fat_max !== null && tg.fat_max !== undefined && !f.relax_fat) {
      lines.push(`<div class="v">只放宽脂肪，仍达不到蛋白质目标。</div>`);
    }
    fallback = `<div class="violations">${lines.join("")}</div>`;
  }

  const totalsLine = `热量 ${num(t.energyKcal, 0)} kcal · 蛋白质 ${num(t.protein)} g ·
    脂肪 ${num(t.fat)} g · 碳水 ${num(t.carbohydrate)} g · 钠 ${num(t.sodium, 0)} mg（官方）`;

  $("s-result").innerHTML = `
    <div class="single-card">
      <div class="single-head">
        <div>
          <b>${s.meal_label || s.meal}</b>
          <span class="badge official">严格模式</span>
          ${s.feasible ? '<span class="badge official">满足硬约束</span>' : '<span class="badge unknown">未满足</span>'}
        </div>
        <div><b>${money}</b></div>
      </div>
      <div class="sub">${singleQuoteLine(q)}</div>
      ${itemRows}
      <div class="nutr"><span>${totalsLine}</span></div>
      ${fallback}
      ${suggestions ? `<details class="sugg-box"><summary>点单建议（点击展开）</summary>${suggestions}
        <div class="sub">去掉配料只写进点单参数；营养数值仍为标准版官方数据。</div></details>` : ""}
      ${upgrade}
      <div class="sub">本版本采用严格模式：营养一律取官方整款标准版；去除配料只影响点单参数，不扣减任何数值。</div>
    </div>`;
  $("s-result-panel").hidden = false;

  document.querySelectorAll("input.srem").forEach((el) => {
    el.addEventListener("change", () => {
      const code = main ? String(main.code) : "";
      if (!code) return;
      const set = new Set(SREMOVED[code] || []);
      if (el.checked) set.add(String(el.dataset.code));
      else set.delete(String(el.dataset.code));
      SREMOVED[code] = [...set];
      singleRecompute();
    });
  });
}

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
    setSStatus(out.feasible
      ? `已找到满足价格和脂肪上限的方案（参考价 ${num(out.cost, 2)} 元）。`
      : "当前条件下无解，已给出可达上限与放宽方案。");
  } catch (e) {
    S = SLAST;
    setSStatus("推荐失败：" + e.message);
  }
}

async function singleRecompute() {
  if (!S) return;
  const main = (S.items || []).find((i) => i.kind === "main");
  if (!main) return;
  const codes = SREMOVED[String(main.code)] || [];
  const custom = codes.length
    ? { [S.meal]: { [main.code]: { action: "suggest_remove", option_codes: codes } } }
    : {};
  try {
    const out = await api("/api/recompute", { customizations: custom });
    S = { ...S, ...out, removable: S.removable, suggestions: S.suggestions,
          upgrade: S.upgrade, frontier: S.frontier, meal: S.meal,
          meal_label: S.meal_label };
    SLAST = S; SSIG = out.signature || "";
    renderSingle(S);
    setSStatus(codes.length
      ? "已加入点单建议。营养仍为标准版官方数值；旧试算价已失效，请重新报价。"
      : "已取消点单建议，恢复标准版。");
  } catch (e) {
    setSStatus("点单建议未生效：" + e.message);
    renderSingle(S);
  }
}

async function singleQuote() {
  if (!S || !SSIG) {
    setSStatus("请先点「推荐这一餐」；报价只针对当前这一餐，不会重新选餐。");
    return;
  }
  setSStatus("正在为当前这一餐试算…");
  try {
    const out = await api("/api/quote", { signature: SSIG });
    S = { ...S, ...out, removable: S.removable, suggestions: S.suggestions,
          upgrade: S.upgrade, frontier: S.frontier, meal: S.meal,
          meal_label: S.meal_label };
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

$("s_meal").addEventListener("change", renderUnknown);
$("s-calc").onclick = singleCalc;
$("s-plan").onclick = singlePlan;
$("s-quote").onclick = singleQuote;
$("btn-save").onclick = async () => {
  try {
    await api("/api/settings", settingsFromForm());
    await boot();
    setStatus("门店/日期/时段已保存。旧方案与旧试算价已失效，请重新推荐。");
  } catch (e) { setStatus("保存失败：" + e.message); }
};
$("btn-refresh").onclick = async () => {
  setStatus("正在按当前门店/日期/时段拉取菜单…");
  try {
    const r = await api("/api/refresh", {});
    await boot();
    setStatus(`刷新完成：${r.date} · 来源 ${r.source} · ${r.fetched_at}。`);
  } catch (e) { setStatus("刷新失败：" + e.message); }
};

async function init() {
  try { await boot(); } catch (e) { setStatus("初始化失败：" + e.message); }
}

init();
