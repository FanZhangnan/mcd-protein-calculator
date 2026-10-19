const $ = (id) => document.getElementById(id);
let BOOT = null;
let PLAN = null;
let QTY = {};        // { meal: { code: qty } }
let CUSTOM = {};     // { meal: { code: {action:'remove_sauce', scenario} } }
let QUOTES = {};
let CAP = {};        // code -> capability payload
let SIG = "";        // signature the quotes belong to

const STATUS_LABEL = {
  official: ["官方", "official"],
  official_alias: ["官方·别名", "alias"],
  estimate: ["估算", "est"],
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
    throw err;
  }
  return data;
}

const setStatus = (t) => { $("status").textContent = t || ""; };
const num = (v, d = 1) => (v === null || v === undefined ? "—" : Number(v).toFixed(d));

const SOURCE_LABEL = {
  manual: "手填", quoted: "采用试算价", unknown: "未知", reference: "参考价",
};
const sourceLabel = (s) => SOURCE_LABEL[s] || s || "未知";
const BASIS_LABEL = {
  quoted: "按试算价", booked: "按已吃记账", reference: "按参考价",
  unknown: "尚未评估", no_budget: "未设预算",
};

function clearPlanUI(reason) {
  PLAN = null; SIG = ""; QUOTES = {}; QTY = {}; CUSTOM = {}; CAP = {};
  $("meals").innerHTML = "";
  $("plan-panel").hidden = true;
  $("summary-panel").hidden = true;
  if (reason) setStatus(reason);
}

function badge(status) {
  const [label, cls] = STATUS_LABEL[status] || [status || "未知", "unknown"];
  return `<span class="badge ${cls}">${label}</span>`;
}

async function boot() {
  BOOT = await api("/api/bootstrap", undefined, "GET");
  const s = BOOT.state;
  $("kcal_min").value = s.targets.kcal_min ?? "";
  $("kcal_max").value = s.targets.kcal_max ?? "";
  $("protein_min").value = s.targets.protein_min ?? "";
  $("fat_max").value = s.targets.fat_max ?? "";
  $("carb_max").value = s.targets.carb_max ?? "";
  $("budget").value = s.targets.budget ?? "";
  $("date").value = s.date || "";
  $("store_code").value = s.store?.code || "";
  $("t_breakfast").value = s.meal_times?.breakfast || "08:15";
  $("t_lunch").value = s.meal_times?.lunch || "12:15";
  $("t_dinner").value = s.meal_times?.dinner || "18:30";

  const sel = $("scenario");
  if (!sel.options.length) {
    BOOT.scenarios.forEach((sc) => {
      const o = document.createElement("option");
      o.value = sc.key; o.textContent = sc.label; sel.appendChild(o);
    });
  }
  sel.value = s.estimate_scenario || "official_only";

  const c = BOOT.cache || {};
  const srcLabel = { live: "实时", cache: "缓存", offline: "离线", none: "未加载", unavailable: "不可用" }[c.source] || c.source;
  $("meta").innerHTML =
    `<span class="pill">${srcLabel}</span> 数据时间 ${c.fetched_at || "—"} · 门店 ${c.store_code || "—"}` +
    ` · 时段 早${c.meal_times?.breakfast || "—"} / 午${c.meal_times?.lunch || "—"} / 晚${c.meal_times?.dinner || "—"}` +
    ` · 营养条目 ${BOOT.catalog.rows || 0}/${BOOT.catalog.unique_names || 0}` +
    (c.error ? ` · <b style="color:#b91c1c">${c.error}</b>` : "");
  renderUnknown();
  renderIntake(BOOT.intake || []);
}

const targetsFromForm = () => {
  const v = (id) => ($(id).value === "" ? null : Number($(id).value));
  return { kcal_min: v("kcal_min"), kcal_max: v("kcal_max"), protein_min: v("protein_min"),
           fat_min: null, fat_max: v("fat_max"), carb_min: null, carb_max: v("carb_max"),
           budget: v("budget") };
};

const settingsFromForm = () => ({
  targets: targetsFromForm(),
  date: $("date").value || null,
  store: { code: $("store_code").value },
  meal_times: { breakfast: $("t_breakfast").value, lunch: $("t_lunch").value, dinner: $("t_dinner").value },
  estimate_scenario: $("scenario").value,
});

function renderSummary(plan) {
  const t = plan.totals || {};
  const gaps = (plan.summary || {}).gaps || {};
  const budget = (plan.summary || {}).budget;
  const cost = (plan.summary || {}).cost;
  const tile = (label, value, unit, gap, gapUnit) => {
    const cls = gap === null || gap === undefined ? "" : (gap < 0 ? "neg" : "pos");
    return `<div class="tile"><b>${value}${unit}</b><span>${label}</span>
      <div class="gap ${cls}">差额 ${gap === null || gap === undefined ? "—" : (gap > 0 ? "+" : "") + gap + gapUnit}</div></div>`;
  };
  const basis = plan.budget_status && plan.budget_status !== "no_budget"
    ? (BASIS_LABEL[plan.budget_status] || plan.budget_status) : "";
  $("totals").innerHTML = [
    tile("全天热量", num(t.energyKcal, 0), " kcal", gaps.kcal, " kcal"),
    tile("蛋白质", num(t.protein), " g", gaps.protein, " g"),
    tile("脂肪", num(t.fat), " g", gaps.fat, " g"),
    tile("碳水", num(t.carbohydrate), " g", gaps.carbo, " g"),
    tile("钠（缺项时不可视为完整）", num(t.sodium, 0), " mg", null, ""),
    tile(`全天合计${basis ? "（" + basis + "）" : ""}`, num(cost, 2), " 元", gaps.budget, " 元"),
  ].join("");
  if (budget !== null && budget !== undefined) {
    $("totals").innerHTML += `<div class="tile"><b>${num(budget, 2)} 元</b><span>日预算</span>
      <div class="gap">参考价合计 ${num(plan.reference_total, 2)} · 试算合计 ${num(plan.quote_total, 2)} · 已吃记账 ${num(plan.booked_total, 2)}</div></div>`;
  }

  const v = plan.violations || [];
  $("violations").innerHTML = v.length
    ? v.map((x) => `<div class="v">未满足 · ${x.label || x.constraint}：实际 ${x.value ?? "未知"}${x.unit || ""}，目标 ${x.target}${x.unit || ""}${x.detail ? " · " + x.detail : ""}</div>`).join("")
      + `<div class="v warn">${plan.note || "以上为最接近方案，<b>不视为达标</b>。"}</div>`
    : `<div class="v warn" style="background:#e8f5ee;border-color:#cbe8d9;color:#0f7b4f">全部硬约束满足</div>`;
  $("summary-panel").hidden = false;
}

function itemNutrition(it) {
  const n = it.nutrition;
  if (!n) return `<span style="color:#b45309">无可用营养数据</span>`;
  const k = it.quantity && it.quantity !== 1 ? ` ×${it.quantity}` : "";
  return `${num(n.energyKcal, 0)} kcal · P${num(n.protein)} F${num(n.fat)} C${num(n.carbohydrate)} · Na ${num(n.sodium, 0)}mg${k}`;
}

function findPlanItem(meal, code) {
  const m = PLAN && PLAN.meals.find((x) => x.meal === meal);
  return m ? m.items.find((i) => String(i.code) === String(code)) : null;
}

function syncQtyFromPlan() {
  // Rebuild QTY from what the screen currently shows, so a rejected edit
  // cannot drop the user's other (valid) changes.
  QTY = {};
  (PLAN && PLAN.meals ? PLAN.meals : []).forEach((m) => {
    if (m.locked) return;
    m.items.forEach((it) => {
      QTY[m.meal] = QTY[m.meal] || {};
      QTY[m.meal][it.code] = it.quantity;
    });
  });
}

function restoreQtyInputs() {
  document.querySelectorAll("input.qty").forEach((el) => {
    const item = findPlanItem(el.dataset.meal, el.dataset.code);
    if (item) el.value = item.quantity;
  });
}

function renderPlan(plan, opts = {}) {
  PLAN = plan;
  if (!opts.keepQuotes) { QUOTES = {}; }
  SIG = plan.signature || SIG;
  renderSummary(plan);
  $("meals").innerHTML = plan.meals.map((m) => {
    const lock = m.locked;
    const items = m.items.map((it) => {
      const est = it.is_estimate ? badge("estimate") : "";
      const qtyVal = it.quantity ?? 1;
      const cap = CAP[it.code];
      const capOptions = (cap && cap.options) || [];
      const hasScenario = capOptions.some((o) => (o.scenarios || []).length > 0);
      const canSauce = cap ? (cap.ok && hasScenario) : it.can_remove_sauce;
      const capReason = cap && cap.ok && !hasScenario
        ? "该酱料只有 POS 特调项，没有可引用的成分数据，无法估算"
        : (cap ? (cap.reason || "") : "");
      const checked = !!(CUSTOM[m.meal] && CUSTOM[m.meal][it.code]);
      const sauceBox = (!lock && it.kind === "main")
        ? `<label class="inline" title="${capReason}">
             <input type="checkbox" class="cust" data-meal="${m.meal}" data-code="${it.code}"
               ${checked ? "checked" : ""} ${canSauce === false ? "disabled" : ""}> 去酱（估算）</label>`
        : "";
      const estBtn = it.is_estimate
        ? `<button class="ghost mini" onclick='showEst(${JSON.stringify(it.estimate)})'>估算依据</button>` : "";
      return `<div class="item">
        <div class="name">${it.name} ${badge(it.nutrition_status)}${est}${lock ? '<span class="badge lock">已锁定</span>' : ""}</div>
        <div class="sub">code ${it.code} · 单价 ${it.price_ref == null ? "—" : it.price_ref + " 元"} · 行计 ${it.line_total_ref == null ? "—" : it.line_total_ref + " 元"}</div>
        <div class="sub">${itemNutrition(it)}</div>
        <div class="sub">数量 <input class="qty" type="number" min="1" step="1" value="${qtyVal}"
             data-meal="${m.meal}" data-code="${it.code}" style="width:70px" ${lock ? "disabled" : ""}>
          <span title="首版只支持整份购买">份（整数）</span>
          ${sauceBox}${estBtn}</div>
      </div>`;
    }).join("");

    const q = m.quote || QUOTES[m.meal];
    const quoteLine = q
      ? (q.status === "ok"
        ? `<div class="sub" style="color:#0f7b4f">试算价 ${q.amount} 元（查询于 ${q.queried_at || "—"}，取餐时点 ${q.reservation_date || "—"}）</div>`
        : `<div class="sub" style="color:#b91c1c">试算失败：${q.message || q.status}</div>`)
      : (lock ? "" : `<div class="sub">未试算 · 仅参考价</div>`);

    const money = lock
      ? (m.amount_actual != null
        ? `记账 ${m.amount_actual} 元（${sourceLabel(m.amount_source)}）`
        : `记账金额未知（预算未评估）`)
      : `参考 ${num(m.price_ref, 2)} 元`;

    return `<div class="meal ${lock ? "locked" : ""}">
      <h4>${m.meal_label}${m.is_estimate ? badge("estimate") : ""}
        <span class="sub">${money}</span></h4>
      ${items}${quoteLine}
      <div class="nutr">
        <span>合计 ${num(m.nutrition.energyKcal, 0)} kcal</span>
        <span>P ${num(m.nutrition.protein)}</span>
        <span>F ${num(m.nutrition.fat)}</span>
        <span>C ${num(m.nutrition.carbohydrate)}</span>
        <span>Na ${num(m.nutrition.sodium, 0)}${(m.unknown || []).includes("sodium") ? "（缺项）" : ""}</span>
      </div>
      ${lock ? "" : `<div class="row"><button class="ghost" onclick="recordMeal('${m.meal}')">记录为已吃</button></div>`}
    </div>`;
  }).join("");
  $("plan-panel").hidden = false;

  document.querySelectorAll("input.qty").forEach((el) => {
    el.addEventListener("change", () => {
      const meal = el.dataset.meal, code = el.dataset.code;
      const raw = String(el.value).trim();
      const n = Number(raw);
      // Purchase counts are whole servings only. Never round or default here:
      // a silently coerced 0.5 would show one number and buy another.
      if (raw === "" || !Number.isFinite(n) || !Number.isInteger(n) || n < 1) {
        const item = findPlanItem(meal, code);
        el.value = item ? item.quantity : 1;
        setStatus(`待买数量必须是正整数（1、2、3…），「${item ? item.name : code}」已恢复为 ${el.value} 份。`
          + "实际只吃了半份，请到下方「已吃记录」里填写。");
        return;
      }
      QTY[meal] = QTY[meal] || {};
      QTY[meal][code] = n;
      recomputeAll();
    });
  });
  document.querySelectorAll("input.cust").forEach((el) => {
    el.addEventListener("change", () => {
      const meal = el.dataset.meal, code = el.dataset.code;
      CUSTOM[meal] = CUSTOM[meal] || {};
      if (el.checked) {
        const cap = CAP[code] || {};
        const option = (cap.options || [])[0] || {};
        if ($("scenario").value === "official_only" && (option.scenarios || []).length) {
          el.checked = false;
          setStatus(`「${option.name || "该酱料"}」没有可直接扣除的官方标准版数据。请先在上方「去酱估算情景」选择一个具名情景，再勾选去酱。`);
          return;
        }
        CUSTOM[meal][code] = { action: "remove_sauce" };
      } else delete CUSTOM[meal][code];
      recomputeAll();
    });
  });
  refreshCapabilities(plan);
}

async function refreshCapabilities(plan) {
  const entries = plan.meals.flatMap((m) => m.items
    .filter((i) => i.kind === "main" && !i.is_estimate)
    .map((i) => ({ code: i.code, meal: m.meal })));
  const unique = [...new Map(entries.map((e) => [String(e.code), e])).values()];
  const missing = unique.filter((e) => CAP[e.code] === undefined);
  if (!missing.length) return;
  await Promise.all(missing.map(async (e) => {
    try {
      CAP[e.code] = await api(
        `/api/capability?code=${encodeURIComponent(e.code)}&meal=${encodeURIComponent(e.meal)}`,
        undefined, "GET");
    } catch { CAP[e.code] = { ok: false, reason: "能力查询失败" }; }
  }));
  if (PLAN === plan) {
    renderPlan(plan, { keepQuotes: true });
  }
}

async function recomputeAll() {
  try {
    const out = await api("/api/recompute", {
      quantities: QTY, customizations: CUSTOM, scenario: $("scenario").value,
    });
    QUOTES = {};
    renderPlan(out);
    if (out.issues && out.issues.length) {
      setStatus("部分定制未生效：" + out.issues.map((i) => `${i.meal}/${i.code} ${i.reason}`).join("；"));
    } else {
      setStatus("已按基线 + 当前数量/定制重算，旧试算价失效，请为当前方案重新报价。");
    }
  } catch (e) {
    if (e.kind === "stale_baseline" || e.kind === "no_baseline") {
      clearPlanUI("门店/日期/时段已变化，旧方案已失效：" + e.message + "请重新生成方案。");
    } else if (e.kind === "invalid_quantity") {
      syncQtyFromPlan();
      restoreQtyInputs();
      setStatus("数量未生效：" + e.message + " 已恢复到当前方案的有效数量；方案与试算价均未变化。");
    } else {
      setStatus("重算失败：" + e.message);
    }
  }
}

function showEst(est) {
  if (!est) return;
  const sc = est.scenario || {};
  const src = sc.source || {};
  const rows = Object.entries(est.removed || {}).filter(([, v]) => v != null)
    .map(([k, v]) => `<div class="kv"><code>${k}</code> 扣除 ${v}</div>`).join("");
  $("est-body").innerHTML = `
    <div class="kv"><b>${sc.label || ""}</b></div>
    <div class="kv">${sc.description || ""}</div>
    <div class="kv">去掉的配料：${est.component || "—"}</div>
    <div class="kv">替代配料：${sc.substitute || "—"}</div>
    <div class="kv">目标配料：${sc.target || "—"}</div>
    <div class="kv">份量假设：${sc.assumption || "—"} <b>（待用户确认）</b></div>
    <div class="kv">来源：${src.publisher || "—"}<br>${src.url || ""}<br>查阅日期：${src.checked || "—"}</div>
    <div class="kv">来源性质：${src.kind || "—"} · ${src.note || ""}</div>
    <hr><div class="kv"><b>计算过程（标准版 − 扣除项）</b></div>${rows}
    <div class="kv">仍无法确定：${(est.unknown || []).join("、") || "无"}</div>`;
  $("est-dialog").showModal();
}

async function recordMeal(meal) {
  const m = PLAN.meals.find((x) => x.meal === meal);
  if (!m) return;
  const quote = QUOTES[meal] || m.quote || null;
  let amount = quote && quote.status === "ok" ? quote.amount : null;
  if (amount === null) {
    const input = prompt(`请填写「${m.meal_label}」实际记账金额（元）。留空表示未知，预算将标记为未评估。`, "");
    if (input !== null && input.trim() !== "") amount = Number(input);
  }
  await api("/api/intake", { meal, items: m.items, quote, amount_actual: amount });
  setStatus(`${m.meal_label} 已记录并锁定。`);
  await boot();
  await doReplan();
}

async function doReplan() {
  try {
    const payload = await api("/api/replan", {});
    QTY = {}; CUSTOM = {}; QUOTES = {};
    renderPlan(payload);
    const rem = payload.remaining || {};
    $("remaining").textContent =
      `已吃：${num(rem.eaten_totals?.energyKcal, 0)} kcal / ${num(rem.eaten_totals?.protein)} g 蛋白 / ${num(rem.eaten_cost, 2)} 元` +
      `（${rem.budget_unknown ? "金额缺项" : "金额完整"}）\n` +
      `剩余目标：${num(rem.remaining?.kcal_min, 0)}–${num(rem.remaining?.kcal_max, 0)} kcal，蛋白 ≥ ${num(rem.remaining?.protein_min)} g，` +
      `预算 ${num(rem.remaining?.budget, 2)} 元\n` +
      `重排餐次：${(payload.open_meals || []).join(" / ") || "无（全部已吃）"}\n` +
      `判定口径：${payload.evaluation?.note || ""}`;
  } catch (e) { setStatus("重排失败：" + e.message); }
}

function renderIntake(records) {
  if (!records.length) { $("intake").innerHTML = `<div class="status">暂无记录</div>`; return; }
  $("intake").innerHTML = records.map((r) => {
    const rows = (r.items || []).map((i) => `
      <div class="sub">${i.name} · ${num(i.nutrition?.energyKcal, 0)} kcal/份 ×
        <input class="req" type="number" min="0.5" step="0.5" value="${i.quantity}"
               data-id="${r.id}" data-code="${i.code}" style="width:64px"> 份（可填 0.5）
        ${i.is_estimate ? badge("estimate") : ""}</div>`).join("");
    return `<div class="rec">
      <div><b>${r.meal}</b> · ${num(r.totals?.energyKcal, 0)} kcal · P${num(r.totals?.protein)}
        <div class="sub">记账金额：${r.amount_actual != null
          ? r.amount_actual + " 元（" + sourceLabel(r.amount_source) + "）"
          : "未知（预算未评估，营养仍计入）"}
          · 参考价 ${num(r.amount_reference, 2)} 元 · 数据源 ${r.data_source || "—"}</div>
        ${rows}
      </div>
      <div style="display:flex;flex-direction:column;gap:4px">
        <button class="ghost mini" onclick="saveRecord('${r.id}')">保存数量</button>
        <button class="ghost mini" onclick="editAmount('${r.id}', ${r.amount_actual ?? "null"})">改记账金额</button>
        <button class="ghost mini" onclick="removeRecord('${r.id}')">撤销</button>
      </div>
    </div>`;
  }).join("");
}

async function saveRecord(id) {
  const inputs = [...document.querySelectorAll(`input.req[data-id="${id}"]`)];
  const record = (BOOT.intake || []).find((r) => r.id === id);
  if (!record) return;
  const bad = [];
  const items = record.items.map((i) => {
    const el = inputs.find((x) => x.dataset.code === String(i.code));
    const raw = el ? String(el.value).trim() : String(i.quantity);
    const n = Number(raw);
    if (raw === "" || !Number.isFinite(n) || n <= 0) bad.push(i.name || i.code);
    return { ...i, quantity: n };
  });
  if (bad.length) {
    setStatus(`已吃数量必须是大于 0 的数字（可以填 0.5）：${bad.join("、")}。本次未保存。`);
    return;
  }
  try {
    await api(`/api/intake/${id}`, { items }, "PATCH");
  } catch (e) {
    setStatus("记录未更新：" + e.message);
    return;
  }
  setStatus("记录已更新，剩余量已重算。");
  await boot();
  await doReplan();
}

async function editAmount(id, current) {
  const input = prompt("实际支付金额（元），留空表示未知", current ?? "");
  if (input === null) return;
  await api(`/api/intake/${id}`, { amount_actual: input.trim() === "" ? null : Number(input) }, "PATCH");
  await boot();
  await doReplan();
}

async function removeRecord(id) {
  await api(`/api/intake/${id}`, undefined, "DELETE");
  setStatus("记录已撤销，剩余量已恢复。");
  await boot();
  await doReplan();
}

function renderUnknown() {
  const unk = BOOT.unknown || {};
  const rows = Object.entries(unk).flatMap(([meal, list]) =>
    (list || []).map((u) => `<tr><td>${meal}</td><td>${u.name}</td><td>${u.code}</td><td>${u.status}</td><td>${u.note || ""}</td></tr>`));
  $("unknown").innerHTML = rows.length
    ? `<div>以下 SKU 无法唯一匹配官方营养，<b>未纳入规划</b>（不会按 0 处理）：</div>
       <table><tr><th>餐次</th><th>名称</th><th>code</th><th>状态</th><th>原因</th></tr>${rows.join("")}</table>`
    : "所有参与规划的 SKU 均有可核实营养来源。";
}

// ------------------------------------------------------------------ bindings
$("btn-save").onclick = async () => {
  try {
    await api("/api/targets", settingsFromForm());
    await boot();
    clearPlanUI("设置已保存。门店/日期/时段或目标已变化，旧方案与旧试算价已失效，请重新生成方案。");
  } catch (e) { setStatus("保存失败：" + e.message); }
};
$("btn-refresh").onclick = async () => {
  setStatus("正在按当前门店/日期/时段拉取菜单…");
  try {
    const r = await api("/api/refresh", { date: $("date").value || null });
    await boot();
    clearPlanUI(`刷新完成：${r.date} · 来源 ${r.source} · ${r.fetched_at}。菜单已更新，请重新生成方案。`);
  }
  catch (e) { setStatus("刷新失败：" + e.message); }
};
$("btn-plan").onclick = async () => {
  setStatus("求解中…");
  try {
    const plan = await api("/api/plan", { targets: targetsFromForm(), quote: false });
    QTY = {}; CUSTOM = {}; QUOTES = {};
    renderPlan(plan);
    SIG = plan.signature || "";
    setStatus(plan.feasible
      ? "已生成方案（金额按参考价）。需要结算参考时点「为当前方案报价」。"
      : "未找到完全可行的方案，已给出最接近方案（不视为达标）。");
  } catch (e) { setStatus("规划失败：" + e.message); }
};
$("btn-quote").onclick = async () => {
  if (!PLAN || !SIG) {
    setStatus("请先点「重新生成方案」；报价只针对当前屏幕上的方案，不会重新选餐。");
    return;
  }
  setStatus("按当前方案逐餐试算（不会替换你的选择）…");
  try {
    const plan = await api("/api/quote", { signature: SIG });
    QUOTES = { ...(plan.quotes || {}) };
    SIG = plan.signature || SIG;
    renderPlan(plan, { keepQuotes: true });
    const label = BASIS_LABEL[plan.budget_status] || plan.budget_status;
    if (plan.budget_status === "no_budget") {
      // a successful quote without a budget is not „not evaluated“
      setStatus(`已完成当前方案试算：合计 ${plan.quote_total} 元；未设置预算，不做预算判定。`);
    } else if (plan.budget_status === "quoted") {
      setStatus(`已完成当前方案试算：合计 ${plan.quote_total} 元（${label}）。`);
    } else {
      setStatus(`预算尚未完成评估（${label}）：${plan.violations.find(v => v.label === "预算")?.detail || "存在失败或缺失金额"}`);
    }
  } catch (e) {
    if (e.kind === "stale_context") {
      clearPlanUI("门店/日期/时段已变化，旧方案已失效：" + e.message + "请重新生成方案。");
    } else if (e.kind === "stale_signature") {
      setStatus("方案已改变，报价请求与当前方案不一致：" + e.message);
    } else if (e.kind === "no_plan") {
      clearPlanUI(e.message);
    } else {
      setStatus("报价失败：" + e.message);
    }
  }
};
$("btn-replan").onclick = async () => { setStatus("按已吃记录重排…"); await doReplan(); };
$("est-close").onclick = () => $("est-dialog").close();
$("scenario").onchange = () => {
  const hasCustom = Object.values(CUSTOM).some((group) => Object.keys(group).length);
  if (PLAN && hasCustom) recomputeAll();
};

// ============================================================ single-meal mode
let SMODE = "single";
let S = null;              // current single-meal payload
let SSIG = "";             // signature of the current single meal
let SREMOVED = {};         // { mainCode: [optionCode, ...] }
let SLAST = null;          // last valid payload, used to restore after a 400

function switchMode(mode) {
  SMODE = mode;
  $("mode-single").classList.toggle("active", mode === "single");
  $("mode-day").classList.toggle("active", mode === "day");
  $("single-mode").hidden = mode !== "single";
  $("day-mode").hidden = mode !== "day";
}

function singleRequest() {
  const body = {
    meal: $("s_meal").value,
    protein_min: Number($("s_protein").value),
    fat_max: Number($("s_fat").value),
    budget: Number($("s_budget").value),
    allow_side: $("s_side").checked,
  };
  const carb = $("s_carb").value.trim();
  if (carb !== "") body.carb_max = Number(carb);
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
  for (const key of ["carb_max", "kcal_min", "kcal_max"]) {
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
  const cut = tdee - 500;
  const per = cut / n;
  const protein = (1.6 * w) / n;
  const fat = Math.max(10, (per * 0.3) / 9);
  $("s_kcal_min").value = Math.round(per * 0.9);
  $("s_kcal_max").value = Math.round(per * 1.1);
  $("s_protein").value = Math.round(protein);
  $("s_fat").value = Math.round(fat);
  if (!$("s_budget").value) $("s_budget").value = 40;
  const floor = sex === "male" ? 1500 : 1200;
  $("s-calc-note").textContent =
    `BMR ${bmr.toFixed(0)} kcal，TDEE ${tdee.toFixed(0)} kcal，减脂目标 ${cut.toFixed(0)} kcal/天，`
    + `每餐约 ${per.toFixed(0)} kcal、蛋白质 ${protein.toFixed(0)} g。`
    + (cut < floor ? ` 注意：全天低于 ${floor} kcal，不建议长期执行。` : "");
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
  const items = (s.meals && s.meals[0] ? s.meals[0].items : []);
  const t = s.totals || {};
  const q = s.quotes && s.quotes[s.meal];
  const main = items.find((i) => i.kind === "main");
  const others = items.filter((i) => i.kind !== "main");
  const money = q && q.status === "ok" ? `试算价 ${q.amount} 元` : `参考价 ${num(s.cost, 2)} 元`;

  const itemRows = items.map((i) => `
    <div class="item">
      <div class="name">${i.name} × ${i.quantity} ${i.kind === "main" ? '<span class="badge official">主食</span>' : ""}</div>
      <div class="sub">${num(i.nutrition?.energyKcal, 0)} kcal · P ${num(i.nutrition?.protein)} g ·
        F ${num(i.nutrition?.fat)} g · C ${num(i.nutrition?.carbohydrate)} g
        · ${num(i.price_ref, 2)} 元 <span class="badge official">官方</span></div>
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
    lines.push(`<div class="v">当前限制下没有满足全部硬约束的方案。${f.message || ""}</div>`);
    if (cap) {
      lines.push(`<div class="v">在脂肪 ≤ ${num(tg.fat_max, 0)} g、价格 ≤ ${num(tg.budget, 2)} 元的条件下，`
        + `这家店这一餐蛋白质最多能达到 <b>${num(cap.protein)} g</b>（${cap.name}，脂肪 ${num(cap.fat)} g，${num(cap.cost, 2)} 元）。</div>`);
    }
    if (f.relax_fat) {
      lines.push(`<div class="v">只放宽脂肪：达到 ${num(tg.protein_min, 0)} g 蛋白质至少需要 `
        + `<b>${num(f.relax_fat.fat)} g 脂肪</b>，对应 ${f.relax_fat.name}（${num(f.relax_fat.cost, 2)} 元）。</div>`);
    } else {
      lines.push(`<div class="v">只放宽脂肪，仍达不到蛋白质目标。</div>`);
    }
    if (f.relax_price) {
      lines.push(`<div class="v">只放宽价格：达到 ${num(tg.protein_min, 0)} g 蛋白质最低需 `
        + `<b>${num(f.relax_price.cost, 2)} 元</b>，对应 ${f.relax_price.name}（脂肪 ${num(f.relax_price.fat)} g）。</div>`);
    } else {
      lines.push(`<div class="v">只放宽价格，仍达不到蛋白质目标。</div>`);
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
      ${suggestions ? `<div class="sugg-box"><b>点单建议（默认不勾选，由你决定）</b>${suggestions}
        <div class="sub">去掉配料只写进点单参数；营养数值仍为标准版官方数据。</div></div>` : ""}
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
  if (bad) { $("s-status").textContent = bad; return; }
  $("s-status").textContent = "正在枚举这一餐的全部候选…";
  try {
    const out = await api("/api/meal", { ...body, quote: false });
    S = out; SLAST = out; SSIG = out.signature || "";
    SREMOVED = {};
    renderSingle(S);
    $("s-status").textContent = out.feasible
      ? `已找到满足价格和脂肪上限的方案（参考价 ${num(out.cost, 2)} 元）。`
      : "当前条件下无解，已给出可达上限与放宽方案。";
  } catch (e) {
    S = SLAST;
    $("s-status").textContent = "推荐失败：" + e.message;
  }
}

async function singleRecompute() {
  if (!S) return;
  const main = S.meals[0].items.find((i) => i.kind === "main");
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
    $("s-status").textContent = codes.length
      ? "已加入点单建议。营养仍为标准版官方数值；旧试算价已失效，请重新报价。"
      : "已取消点单建议，恢复标准版。";
  } catch (e) {
    $("s-status").textContent = "点单建议未生效：" + e.message;
    renderSingle(S);
  }
}

async function singleQuote() {
  if (!S || !SSIG) {
    $("s-status").textContent = "请先点「推荐这一餐」；报价只针对当前这一餐，不会重新选餐。";
    return;
  }
  $("s-status").textContent = "正在为当前这一餐试算…";
  try {
    const out = await api("/api/quote", { signature: SSIG });
    S = { ...S, ...out, removable: S.removable, suggestions: S.suggestions,
          upgrade: S.upgrade, frontier: S.frontier, meal: S.meal,
          meal_label: S.meal_label };
    SLAST = S; SSIG = out.signature || "";
    renderSingle(S);
    // /api/quote does not echo `meal`; use the one we are looking at
    const q = out.quotes && out.quotes[S.meal];
    $("s-status").textContent = q && q.status === "ok"
      ? `试算成功：${q.amount} 元（查询于 ${q.queried_at || "—"}；取餐时点 ${q.reservation_date || "—"}）。`
      : `试算失败：${(q && q.message) || "接口未返回金额"}。`;
  } catch (e) {
    $("s-status").textContent = "试算失败：" + e.message;
  }
}

$("mode-single").onclick = () => switchMode("single");
$("mode-day").onclick = () => switchMode("day");
$("s-calc").onclick = singleCalc;
$("s-plan").onclick = singlePlan;
$("s-quote").onclick = singleQuote;

async function init() {
  switchMode("single");
  try { await boot(); } catch (e) { setStatus("初始化失败：" + e.message); }
}

init();

