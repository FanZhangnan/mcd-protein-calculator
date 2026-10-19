// Real browser flow via Chrome DevTools Protocol (Node 22 built-in WebSocket).
// Usage: node tests/browser_flow.mjs [url]
//
// Flow: set targets -> plan -> quote current (keeps edits) -> positive remove-sauce
//       -> cancel -> change quantity -> quote again -> record -> edit -> replan
//       -> finish all meals -> delete one -> switch store.
import { spawn } from "node:child_process";

const URL_ = process.argv[2] || "http://127.0.0.1:8771/";
const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PORT = 9333;

const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"} · ${name}${detail ? " · " + detail : ""}`);
};

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const proc = spawn(CHROME, [
    "--headless=new", "--disable-gpu", "--no-sandbox", "--no-first-run",
    `--remote-debugging-port=${PORT}`, "--user-data-dir=/tmp/chrome-cdp-profile",
    "about:blank",
  ], { stdio: "ignore" });
  await sleep(2500);

  const version = await (await fetch(`http://127.0.0.1:${PORT}/json/version`)).json();
  check("Chrome CDP 可用", !!version.webSocketDebuggerUrl, version.Browser);

  const tab = await (await fetch(`http://127.0.0.1:${PORT}/json/new?${encodeURIComponent(URL_)}`,
    { method: "PUT" })).json();
  const ws = new WebSocket(tab.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });

  let id = 0;
  const pending = new Map();
  ws.onmessage = (msg) => {
    const data = JSON.parse(msg.data);
    if (data.id && pending.has(data.id)) { pending.get(data.id)(data); pending.delete(data.id); }
  };
  const send = (method, params = {}) => new Promise((resolve) => {
    const mid = ++id;
    pending.set(mid, resolve);
    ws.send(JSON.stringify({ id: mid, method, params }));
  });

  await send("Page.enable");
  await send("Runtime.enable");

  // auto-accept the amount prompt
  let dialogText = "20";
  ws.addEventListener("message", async (msg) => {
    const data = JSON.parse(msg.data);
    if (data.method === "Page.javascriptDialogOpening") {
      await send("Page.handleJavaScriptDialog", { accept: true, promptText: dialogText });
    }
  });

  const evalJs = async (expr) => {
    const r = await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true });
    if (r.result?.exceptionDetails) {
      console.log("JS ERROR", JSON.stringify(r.result.exceptionDetails).slice(0, 300));
    }
    return r.result?.result?.value;
  };
  const evalJson = async (expr) => JSON.parse(await evalJs(`JSON.stringify(${expr})`) || "null");
  const waitFor = async (expr, label, timeout = 30000) => {
    const t0 = Date.now();
    while (Date.now() - t0 < timeout) {
      if (await evalJs(expr)) return true;
      await sleep(400);
    }
    check(`等待超时：${label}`, false, expr.slice(0, 60));
    return false;
  };

  await send("Page.navigate", { url: URL_ });
  await waitFor(`document.querySelector('#meta') && document.querySelector('#meta').textContent.includes('门店')`, "页面加载");

  const meta0 = await evalJs(`document.querySelector('#meta').textContent`);
  check("页面渲染并显示门店", /门店\s*\d+/.test(meta0), meta0.slice(0, 100));

  // 1) settings: a target set whose plan contains 麦香鸡 (sauce-capable)
  await evalJs(`
    (() => {
      const set = (id, v) => { const el = document.getElementById(id); el.value = v; };
      set('kcal_min', 1600); set('kcal_max', 1900); set('protein_min', 70);
      set('fat_max', 90); set('carb_max', 260); set('budget', 100);
      set('date', '2026-10-20');
      set('store_code', '1440054');
      set('t_breakfast', '08:15'); set('t_lunch', '12:15'); set('t_dinner', '18:30');
      document.getElementById('btn-save').click();
      return true;
    })()
  `);
  await waitFor(`document.querySelector('#status').textContent.includes('请重新生成方案')`, "保存目标", 45000);
  check("保存目标后清空旧方案并提示重规划",
        (await evalJs(`document.getElementById('plan-panel').hidden`)) === true &&
        (await evalJs(`document.querySelector('#status').textContent`)).includes("重新生成方案"),
        await evalJs(`document.querySelector('#status').textContent`));

  // 2) plan
  await evalJs(`document.getElementById('btn-plan').click()`);
  await waitFor(`!document.getElementById('plan-panel').hidden && document.querySelectorAll('.meal').length===3`, "规划", 60000);
  const planState = await evalJson(`(PLAN ? PLAN.meals.map(m => ({meal: m.meal, locked: m.locked,
      items: m.items.map(i => [i.code, i.quantity])})) : null)`);
  check("生成三餐卡片", (await evalJs(`document.querySelectorAll('.meal').length`)) === 3,
        JSON.stringify(planState));
  check("每餐至少 1 个商品", planState.every((m) => m.items.length >= 1),
        planState.map((m) => `${m.meal}:${m.items.length}`).join(" | "));
  const planSig = await evalJs(`SIG`);
  check("方案有签名", typeof planSig === "string" && planSig.length === 16, planSig);

  // 3) quote the plan on screen (must not re-solve)
  await evalJs(`document.getElementById('btn-quote').click()`);
  await waitFor(`/已完成当前方案试算|预算尚未完成评估|报价失败/.test(document.querySelector('#status').textContent)`,
                "当前方案报价", 120000);
  const quoteState = await evalJson(`(PLAN ? {
      sig: SIG,
      quoteTotal: PLAN.quote_total,
      quotes: PLAN.meals.map(m => m.quote ? [m.meal, m.quote.status, m.quote.amount, m.quote.queried_at] : [m.meal, null]),
      items: PLAN.meals.map(m => [m.meal, m.items.map(i => [i.code, i.quantity])])
    } : null)`);
  check("三笔试算均成功", quoteState.quotes.filter((q) => q[1] === "ok").length === 3,
        JSON.stringify(quoteState.quotes));
  check("试算行显示查询时间", quoteState.quotes.every((q) => q[3]),
        JSON.stringify(quoteState.quotes.map((q) => q[3])));
  check("报价后商品与数量不变",
        JSON.stringify(quoteState.items) === JSON.stringify(planState.map((m) => [m.meal, m.items])),
        JSON.stringify(quoteState.items));
  check("页面出现三条试算行",
        (await evalJs(`[...document.querySelectorAll('.meal .sub')].filter(e=>e.textContent.includes('试算价')).length`)) === 3,
        String(await evalJs(`[...document.querySelectorAll('.meal .sub')].filter(e=>e.textContent.includes('试算价')).length`)));

  // 4) positive remove-sauce on a SKU that really exposes 麦香鸡酱
  //    a named estimate scenario is a real product input; 仅官方数据无法扣除酱料
  await evalJs(`(() => { const el = document.getElementById('scenario');
      el.value = 'tw_sauce_serving';
      el.dispatchEvent(new Event('change', {bubbles:true})); return el.value; })()`);
  const sauce = await evalJson(`(() => {
      for (const [code, cap] of Object.entries(CAP)) {
        const opt = (cap.options || []).find(o => o.name.includes('麦香鸡酱'));
        if (opt) return {code, name: opt.name};
      }
      return null;
    })()`);
  check("方案中存在麦香鸡酱主食", sauce !== null, JSON.stringify(sauce));
  let sauceMeal = null, sauceKcal = null;
  if (sauce) {
    sauceMeal = await evalJs(`PLAN.meals.find(m => m.items.some(i => i.code === '${sauce.code}')).meal`);
    sauceKcal = await evalJs(`PLAN.meals.find(m => m.meal === '${sauceMeal}').nutrition.energyKcal`);
    await evalJs(`(() => {
        const el = document.querySelector('input.cust[data-code="${sauce.code}"][data-meal="${sauceMeal}"]');
        el.checked = true; el.dispatchEvent(new Event('change', {bubbles:true})); return true;
      })()`);
    await waitFor(`(() => {
        const m = PLAN && PLAN.meals.find(x => x.meal === '${sauceMeal}');
        const it = m && m.items.find(i => i.code === '${sauce.code}');
        return !!(it && it.is_estimate);
      })()`, "去酱重算", 45000);
    const after = await evalJson(`(() => {
        const m = PLAN.meals.find(x => x.meal === '${sauceMeal}');
        const it = m.items.find(i => i.code === '${sauce.code}');
        return {kcal: m.nutrition.energyKcal, sodium: m.nutrition.sodium,
                est: !!it.is_estimate, mod: it.modification,
                badges: document.querySelectorAll('.badge.est').length};
      })()`);
    check("去酱生效：热量下降", after.kcal < sauceKcal, `${sauceKcal} -> ${after.kcal}`);
    check("去酱生效：钠变为缺项", after.sodium === null, String(after.sodium));
    check("去酱生效：估算标记", after.est === true && after.badges > 0, JSON.stringify(after));
    check("去酱生效：定制参数进入方案", Array.isArray(after.mod) && after.mod.length === 1,
          JSON.stringify(after.mod));
    check("去酱后旧试算失效", (await evalJs(`[...document.querySelectorAll('.meal .sub')].filter(e=>e.textContent.includes('试算价')).length`)) === 0,
          "no 试算价 line");

    // cancel
    await evalJs(`(() => {
        const el = document.querySelector('input.cust[data-code="${sauce.code}"][data-meal="${sauceMeal}"]');
        el.checked = false; el.dispatchEvent(new Event('change', {bubbles:true})); return true;
      })()`);
    await waitFor(`(() => {
        const m = PLAN && PLAN.meals.find(x => x.meal === '${sauceMeal}');
        const it = m && m.items.find(i => i.code === '${sauce.code}');
        return !!(it && !it.is_estimate && Math.abs(m.nutrition.energyKcal - ${sauceKcal}) < 0.01);
      })()`, "取消去酱", 45000);
    const restored = await evalJson(`(() => {
        const m = PLAN.meals.find(x => x.meal === '${sauceMeal}');
        const it = m.items.find(i => i.code === '${sauce.code}');
        return {kcal: m.nutrition.energyKcal, est: !!it.is_estimate,
                badges: document.querySelectorAll('.badge.est').length,
                customEmpty: !CUSTOM['${sauceMeal}'] || !CUSTOM['${sauceMeal}']['${sauce.code}']};
      })()`);
    check("取消去酱完整恢复", Math.abs(restored.kcal - sauceKcal) < 0.01 && restored.est === false,
          `${restored.kcal} vs ${sauceKcal}`);
    check("取消后无估算标记", restored.badges === 0, String(restored.badges));
    check("取消后定制为空", restored.customEmpty === true, JSON.stringify(restored));
  }

  // 4.5) fractional purchase counts are rejected by the UI, never rounded
  const qtyGuard = JSON.parse(await evalJs(`(async () => {
      const el = document.querySelector('input.qty');
      const meal = el.dataset.meal, code = el.dataset.code;
      const read = () => {
        const m = PLAN.meals.find(x => x.meal === meal);
        return m.items.find(i => String(i.code) === String(code)).quantity;
      };
      const before = {sig: SIG, qty: read()};
      const results = [];
      for (const bad of ['0.5', '1.5', '2.5', '0', '-1', '']) {
        el.value = bad;
        el.dispatchEvent(new Event('change', {bubbles: true}));
        await new Promise(r => setTimeout(r, 250));
        results.push({bad, shown: el.value, status: document.querySelector('#status').textContent});
      }
      return JSON.stringify({before, after: {sig: SIG, qty: read()}, results});
    })()`));
  check("待买小数/0/负数/空值被界面拒收并恢复",
        qtyGuard.results.every((r) => Number(r.shown) === qtyGuard.before.qty
                                      && /正整数/.test(r.status)),
        JSON.stringify(qtyGuard.results).slice(0, 220));
  check("非法待买输入不改变方案与签名",
        qtyGuard.after.sig === qtyGuard.before.sig
        && qtyGuard.after.qty === qtyGuard.before.qty,
        `${qtyGuard.before.sig} -> ${qtyGuard.after.sig}`);

  // 5) quantity edit survives a re-quote
  const qtyEdit = await evalJson(`(() => {
      const el = document.querySelector('input.qty');
      const meal = el.dataset.meal, code = el.dataset.code;
      el.value = 2; el.dispatchEvent(new Event('change', {bubbles:true}));
      return {meal, code};
    })()`);
  await waitFor(`(() => {
      const m = PLAN && PLAN.meals.find(x => x.meal === '${qtyEdit.meal}');
      const it = m && m.items.find(i => i.code === '${qtyEdit.code}');
      return !!(it && it.quantity === 2);
    })()`, "数量重算", 45000);
  const afterQty = await evalJson(`(() => {
      const m = PLAN.meals.find(x => x.meal === '${qtyEdit.meal}');
      const it = m.items.find(i => i.code === '${qtyEdit.code}');
      return {qty: it.quantity, kcal: m.nutrition.energyKcal, sig: SIG};
    })()`);
  check("改数量进入当前方案", afterQty.qty === 2, JSON.stringify(afterQty));

  await evalJs(`document.getElementById('btn-quote').click()`);
  await waitFor(`/已完成当前方案试算|预算尚未完成评估/.test(document.querySelector('#status').textContent)`,
                "改数量后报价", 120000);
  const quotedQty = await evalJson(`(() => {
      const m = PLAN.meals.find(x => x.meal === '${qtyEdit.meal}');
      const it = m.items.find(i => i.code === '${qtyEdit.code}');
      return {qty: it.quantity, amount: m.quote ? m.quote.amount : null,
              status: m.quote ? m.quote.status : null,
              statusText: document.querySelector('#status').textContent};
    })()`);
  check("报价保留修改后的数量", quotedQty.qty === 2, JSON.stringify(quotedQty));
  check("修改后的方案报价成功", quotedQty.status === "ok", JSON.stringify(quotedQty));
  check("提示文案为试算/参考，不称实付",
        !(await evalJs(`document.querySelector('#status').textContent.includes('实付')`)),
        quotedQty.statusText.slice(0, 80));

  // 6) record breakfast (quote adopted as booked amount)
  await evalJs(`(() => { const b=[...document.querySelectorAll('button')].find(x=>x.textContent.includes('记录为已吃')); b && b.click(); return !!b; })()`);
  await waitFor(`document.querySelectorAll('.rec').length > 0
                 && PLAN && PLAN.meals.filter(m => m.locked).length === 1`, "记录已吃", 60000);
  const recState = await evalJson(`(() => {
      const rows = [...document.querySelectorAll('.rec')];
      return rows.map(r => r.textContent.replace(/\\s+/g,' '));
    })()`);
  check("记录早餐成功", recState.length === 1, recState[0]?.slice(0, 80));
  check("记录显示记账金额与来源", /记账金额：\d+(\.\d+)? 元（采用试算价）/.test(recState[0]), recState[0].slice(0, 120));

  const planAfterRecord = await evalJson(`(PLAN ? PLAN.meals.map(m => ({meal: m.meal, locked: m.locked,
      amount: m.amount_actual, source: m.amount_source, qty: m.items.map(i => i.quantity)})) : null)`);
  const lockedNow = planAfterRecord.filter((m) => m.locked);
  check("规划里早餐被锁定", lockedNow.length === 1 && lockedNow[0].meal === "breakfast",
        JSON.stringify(planAfterRecord));
  check("锁定餐显示记账金额", typeof lockedNow[0].amount === "number", JSON.stringify(lockedNow[0]));
  check("餐次数仍为 3", planAfterRecord.length === 3, String(planAfterRecord.length));

  // 7) edit the record quantity
  const qtyBefore = await evalJs(`BOOT.intake[0].items[0].quantity`);
  await evalJs(`(() => { const el = document.querySelector('input.req'); el.value = (Number(el.value)||1)+1; document.querySelector('button[onclick^="saveRecord"]').click(); return true; })()`);
  await waitFor(`BOOT.intake[0] && BOOT.intake[0].items[0].quantity === ${qtyBefore + 1}`, "保存数量", 60000);
  const edited = await evalJson(`(BOOT.intake && BOOT.intake[0]) ? {
      qty: BOOT.intake[0].items.map(i => i.quantity),
      total: BOOT.intake[0].totals.energyKcal,
      amount: BOOT.intake[0].amount_actual,
      source: BOOT.intake[0].amount_source
    } : null`);
  check("修改记录数量后营养按数量重算", edited.qty[0] === lockedNow[0].qty[0] + 1,
        JSON.stringify(edited));
  check("改数量后旧记账金额作废（需重填）", edited.amount === null && edited.source === "unknown",
        JSON.stringify(edited));

  // 8) replan shows remaining + only open meals
  await evalJs(`document.getElementById('btn-replan').click()`);
  await waitFor(`(() => {
      const m = PLAN && PLAN.meals.find(x => x.meal === 'breakfast');
      return !!(m && m.locked && m.items[0].quantity === ${qtyBefore + 1});
    })()`, "重排", 90000);
  const replanInfo = await evalJson(`(() => ({
      open: PLAN.meals.filter(m => !m.locked).map(m => m.meal),
      count: PLAN.meals.length,
      remaining: document.querySelector('#remaining').textContent
    }))()`);
  check("重排只开放午晚", JSON.stringify(replanInfo.open) === '["lunch","dinner"]',
        JSON.stringify(replanInfo.open));
  check("重排后仍是 3 餐", replanInfo.count === 3, String(replanInfo.count));
  check("重排显示金额缺项", replanInfo.remaining.includes("金额缺项"),
        replanInfo.remaining.slice(0, 90));

  // 9) finish lunch and dinner
  for (let i = 0; i < 2; i++) {
    await evalJs(`(() => { const b=[...document.querySelectorAll('button')].find(x=>x.textContent.includes('记录为已吃')); b && b.click(); return !!b; })()`);
    await waitFor(`document.querySelectorAll('.rec').length >= ${i + 2}
                   && PLAN && PLAN.meals.filter(m => m.locked).length === ${i + 2}`, `记录第 ${i + 2} 餐`, 60000);
  }
  const allDone = await evalJson(`(() => ({
      meals: PLAN ? PLAN.meals.length : -1,
      locked: PLAN ? PLAN.meals.filter(m => m.locked).length : -1,
      names: PLAN ? PLAN.meals.map(m => [m.meal, m.locked]) : [],
      remaining: document.querySelector('#remaining').textContent,
      cards: document.querySelectorAll('.meal').length
    }))()`);
  check("三餐全吃后仍只有 3 条", allDone.meals === 3 && allDone.cards === 3,
        JSON.stringify(allDone.names));
  check("三餐全吃后全部锁定", allDone.locked === 3, JSON.stringify(allDone.names));
  check("三餐全吃后提示无重排餐次", allDone.remaining.includes("无（全部已吃）"),
        allDone.remaining.slice(0, 100));

  // 10) delete dinner -> only dinner reopens
  await evalJs(`(() => { const bs=[...document.querySelectorAll('button')].filter(x=>x.textContent.includes('撤销')); bs[bs.length-1] && bs[bs.length-1].click(); return !!bs.length; })()`);
  await waitFor(`document.querySelectorAll('.rec').length === 2
                 && PLAN && PLAN.meals.filter(m => m.locked).length === 2`, "撤销晚餐", 60000);
  const reopened = await evalJson(`(() => ({
      open: PLAN.meals.filter(m => !m.locked).map(m => m.meal),
      count: PLAN.meals.length
    }))()`);
  check("撤销一餐后只开放该餐", JSON.stringify(reopened.open) === '["dinner"]',
        JSON.stringify(reopened.open));

  // 11) switch store -> plan must be cleared
  await evalJs(`
    (() => {
      document.getElementById('store_code').value = '1450713';
      document.getElementById('t_breakfast').value = '09:00';
      document.getElementById('btn-save').click();
      return true;
    })()
  `);
  await waitFor(`document.querySelector('#meta').textContent.includes('1450713')`, "切换门店", 60000);
  const afterSwitch = await evalJson(`(() => ({
      hidden: document.getElementById('plan-panel').hidden,
      sig: SIG,
      status: document.querySelector('#status').textContent
    }))()`);
  check("切换门店后清空当前方案", afterSwitch.hidden === true && afterSwitch.sig === "",
        JSON.stringify(afterSwitch));

  // back to Zhuhai for the next run
  await evalJs(`(() => { document.getElementById('store_code').value='1440054'; document.getElementById('t_breakfast').value='08:15'; document.getElementById('btn-save').click(); return true; })()`);
  await sleep(800);

  try { ws.close(); } catch {}
  try { proc.kill("SIGKILL"); } catch {}

  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} browser checks passed`);
  process.exit(failed.length ? 1 : 0);
}

main().catch((e) => { console.error("FATAL", e); process.exit(2); });
