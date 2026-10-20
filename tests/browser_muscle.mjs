// 麦当劳增肌计算器 UI, real Chrome via CDP.
// Usage: node tests/browser_muscle.mjs [url]
//
// 必须跑在 **离线桩服务**（--offline）上：下面的「省钱选项」断言依赖离线
// fixture 里存在更便宜的容差组合，实时菜单的组合空间不同，可能没有 saver。
//
//   MAI_MAI_DATA=/tmp/mai-off node src/server.py --offline --port 8797
//   node tests/browser_muscle.mjs http://127.0.0.1:8797/
//
// Covers: scatter plot + top-3 + hover, target region, body-data prefill,
// positive flow (N=2), removal suggestion + quote, no-solution fallback card,
// and the "no combo under caps -> no card" branch.
import { spawn } from "node:child_process";
import { waitForJson } from "./cdp_ready.mjs";

const URL_ = process.argv[2] || "http://127.0.0.1:8773/";
const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PORT = 9335;

const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"} · ${name}${detail ? " · " + detail : ""}`);
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const proc = spawn(CHROME, [
    "--headless=new", "--disable-gpu", "--no-sandbox", "--no-first-run", "--no-proxy-server",
    `--remote-debugging-port=${PORT}`, "--user-data-dir=/tmp/chrome-cdp-muscle",
    "about:blank",
  ], { stdio: "ignore" });
  await sleep(2500);

  const version = await waitForJson(`http://127.0.0.1:${PORT}/json/version`);
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

  const evalJs = async (expr) => {
    const r = await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true });
    if (r.result?.exceptionDetails) console.log("JS ERROR", JSON.stringify(r.result.exceptionDetails).slice(0, 300));
    return r.result?.result?.value;
  };
  const waitFor = async (expr, label, timeout = 90000) => {
    const t0 = Date.now();
    while (Date.now() - t0 < timeout) {
      if (await evalJs(expr)) return true;
      await sleep(400);
    }
    check(`等待超时：${label}`, false, expr.slice(0, 80));
    return false;
  };

  await send("Page.navigate", { url: URL_ });
  await waitFor(`document.querySelector('#meta') && document.querySelector('#meta').textContent.includes('门店')`, "页面加载");
  await waitFor(`typeof LAND !== 'undefined' && LAND && LAND.items && LAND.items.length > 0`, "性价比图");

  check("标题与副标题",
        (await evalJs(`document.title === '麦当劳增肌计算器'
          && document.body.textContent.includes('这一顿，想吃多少蛋白质？')
          && document.querySelector('.hero-sub').textContent.length > 10`)) === true);

  // --------------------------------------------------------------- chart
  const chart = await evalJs(`JSON.stringify({
      dots: document.querySelectorAll('#chart circle.dot').length,
      hot: document.querySelectorAll('#chart circle.hot').length,
      nums: document.querySelectorAll('#chart text.dot-num').length,
      rows: document.querySelectorAll('#rank-table tr').length - 1,
      items: LAND.items.length
    })`).then(JSON.parse);
  check("散点图为主食数量", chart.dots === chart.items, JSON.stringify(chart));
  check("前 3 高亮并编号", chart.hot === Math.min(3, chart.items) && chart.nums === chart.hot,
        JSON.stringify(chart));
  check("排行表默认最多 5 行", chart.rows === Math.min(5, chart.items), String(chart.rows));
  await evalJs(`document.querySelector('#rank-toggle').click()`);
  const expanded = await evalJs(`document.querySelectorAll('#rank-table tr').length - 1`);
  check("展开后最多 10 行", expanded === Math.min(10, chart.items), String(expanded));
  await evalJs(`document.querySelector('#rank-toggle').click()`);

  await evalJs(`document.querySelector('#s_budget').value = 45;
                document.querySelector('#s_budget').dispatchEvent(new Event('input'))`);
  check("达标区域已绘制", (await evalJs(`!!document.querySelector('#chart rect.region')`)) === true);

  await evalJs(`(() => {
      const dot = document.querySelector('#chart circle.dot');
      dot.dispatchEvent(new MouseEvent('mouseenter', {bubbles: true}));
      dot.dispatchEvent(new MouseEvent('mousemove', {bubbles: true, clientX: 200, clientY: 150}));
    })()`);
  await sleep(300);
  const tip = await evalJs(`(() => {
      const t = document.querySelector('#chart-tip');
      return JSON.stringify({hidden: t.hidden, text: t.textContent});
    })()`).then(JSON.parse);
  check("悬停显示名称/价格/蛋白质/脂肪/热量",
        tip.hidden === false && ["价格", "蛋白质", "脂肪", "热量"].every((k) => tip.text.includes(k)),
        tip.text.slice(0, 90));

  // ---------------------------------------------------------- body estimate
  await evalJs(`(() => {
      document.querySelector('#s_sex').value = 'male';
      document.querySelector('#s_age').value = 22;
      document.querySelector('#s_height').value = 175;
      document.querySelector('#s_weight').value = 70;
      document.querySelector('#s_meals').value = 3;
      document.querySelector('#s_goal').value = 'muscle';
      document.querySelector('#s-calc').click();
    })()`);
  await sleep(300);
  const body = await evalJs(`JSON.stringify({
      protein: document.querySelector('#s_protein').value,
      kcalMax: document.querySelector('#s_kcal_max').value,
      hint: document.querySelector('#s-cap-hint').textContent,
      note: document.querySelector('#s-calc-note').textContent
    })`).then(JSON.parse);
  check("身体数据预填蛋白质 47 g", body.protein === "47", body.protein);
  check("热量只作参考、不预填约束", body.kcalMax === "", JSON.stringify(body.kcalMax));
  check("显示预算内蛋白质上限提示",
        body.hint.includes("最多能买到") && body.hint.includes("g 蛋白质"), body.hint);

  // ---------------------------------------------------------- positive flow
  await evalJs(`(() => {
      document.querySelector('#s_protein').value = 40;
      document.querySelector('#s_budget').value = 50;
      document.querySelector('#s_fat').value = '';
      document.querySelector('#s_max_mains').value = '2';
      document.querySelector('#s_side').checked = false;
      document.querySelector('#s-plan').click();
    })()`);
  await waitFor(`typeof S !== 'undefined' && S && S.items`, "增肌方案", 90000);
  const pos = await evalJs(`JSON.stringify({
      feasible: S.feasible,
      mains: S.items.filter(i => i.kind === 'main').length,
      qty: S.items.filter(i => i.kind === 'main').reduce((a, i) => a + i.quantity, 0),
      protein: S.totals.protein, cost: S.cost,
      ppy: S.protein_per_yuan,
      ppyText: document.querySelector('#s-result').textContent.includes('每元蛋白质')
    })`).then(JSON.parse);
  check("正向：达标且最多 2 个主食",
        pos.feasible === true && pos.mains >= 1 && pos.mains <= 2 && pos.qty <= 2,
        JSON.stringify(pos));
  check("正向：每元蛋白质保留一位小数",
        Math.abs(pos.ppy - Math.round((pos.protein / pos.cost) * 10) / 10) < 0.05 && pos.ppyText,
        `${pos.ppy}`);
  check("结果卡显示官方标准版与达标",
        (await evalJs(`document.querySelector('#s-result').textContent.includes('官方标准版')
                       && document.querySelector('#s-result').textContent.includes('达标')`)) === true);

  // ------------------------------------------------- removal + quote (N=1)
  await evalJs(`(() => {
      document.querySelector('#s_protein').value = 20;
      document.querySelector('#s_budget').value = 24;
      document.querySelector('#s_fat').value = 18;
      document.querySelector('#s_max_mains').value = '1';
      document.querySelector('#s-plan').click();
    })()`);
  await waitFor(`S && S.items && S.items.length === 1 && (S.suggestions || []).length > 0`, "带点单建议的方案");
  const beforeKcal = await evalJs(`S.totals.energyKcal`);
  await evalJs(`(() => {
      const d = document.querySelector('details.sugg-box');
      if (d) d.open = true;
      const el = document.querySelector('input.srem');
      if (!el) return false;
      el.checked = true;
      el.dispatchEvent(new Event('change', {bubbles: true}));
      return true;
    })()`);
  await waitFor(`S && S.items[0].nutrition_basis === 'standard_unadjusted'`, "点单建议重算");
  const removed = await evalJs(`JSON.stringify({
      kcal: S.totals.energyKcal, protein: S.totals.protein,
      mod: S.items[0].modification, notes: S.items[0].removal_notes
    })`).then(JSON.parse);
  check("去料后营养不变", removed.kcal === beforeKcal);
  check("严格模式文案", removed.notes.some((n) => n.includes("标准版数值")),
        JSON.stringify(removed.notes).slice(0, 100));

  await evalJs(`document.querySelector('#s-quote').click()`);
  await waitFor(`S && S.quotes && S.quotes.lunch && S.quotes.lunch.status === 'ok'`, "去料后报价");
  const quote = await evalJs(`JSON.stringify({
      amount: S.quotes.lunch.amount,
      requested: S.quotes.lunch.requested_items[0],
      status: document.querySelector('#s-status').textContent
    })`).then(JSON.parse);
  check("报价带整组去料参数", quote.requested.modification.values.length === removed.mod.length,
        JSON.stringify(quote.requested.modification).slice(0, 120));
  check("报价显示试算价", quote.status.includes("试算成功"), quote.status.slice(0, 60));

  // ------------------------------------------------------- no-solution card
  await evalJs(`(() => {
      document.querySelector('#s_protein').value = 99;
      document.querySelector('#s_budget').value = 35;
      document.querySelector('#s_fat').value = 20;
      document.querySelector('#s_max_mains').value = '2';
      document.querySelector('#s-plan').click();
    })()`);
  await waitFor(`S && S.feasible === false && S.frontier`, "无解方案");
  const neg = await evalJs(`JSON.stringify({
      text: document.querySelector('#s-result').textContent,
      capBadge: document.querySelector('#s-result').textContent.includes('预算内最多'),
      cost: S.cost, fat: S.totals.fat,
      items: S.items.length
    })`).then(JSON.parse);
  check("无解时只留一条预算内最多的说明",
        neg.text.includes("最多只能做到") && !neg.text.includes("当前条件下没有满足全部要求的方案"),
        neg.text.slice(0, 80));
  check("无解主卡片=预算内最多且不超价格/脂肪",
        neg.capBadge && neg.items > 0 && neg.cost <= 35 && neg.fat <= 20,
        JSON.stringify({cost: neg.cost, fat: neg.fat}));
  check("放宽说明改成浅灰一段而不是红色提示",
        (await evalJs(`document.querySelectorAll('#s-result .violations').length`)) === 0
        && (await evalJs(`!!document.querySelector('#s-result .under-note')`)) === true,
        neg.text.slice(-120));

  // ---------------------------------------------------- no card under caps
  await evalJs(`(() => {
      document.querySelector('#s_budget').value = 5;
      document.querySelector('#s-plan').click();
    })()`);
  await waitFor(`S && S.feasible === false && S.items.length === 0`, "无卡片分支");
  const noCard = await evalJs(`JSON.stringify({
      text: document.querySelector('#s-result').textContent,
      cards: document.querySelectorAll('#s-result .item').length
    })`).then(JSON.parse);
  check("硬约束下无组合时不显示商品卡片",
        noCard.cards === 0 && noCard.text.includes("未选到商品"),
        JSON.stringify(noCard.cards));

  const money = await evalJs(`document.querySelector('#s-result .single-head').textContent`);
  check("无商品时不显示 0.00 元", !/0\.00\s*元/.test(money) && money.includes("未选到商品"),
        money.replace(/\s+/g, " ").slice(0, 70));

  // -------------------------------------------------- zero protein rejected
  await evalJs(`(() => {
      document.querySelector('#s_protein').value = 0;
      document.querySelector('#s-plan').click();
    })()`);
  await sleep(300);
  check("蛋白质填 0 时前端拦截",
        (await evalJs(`document.querySelector('#s-status').textContent`)).includes("必须大于 0"),
        await evalJs(`document.querySelector('#s-status').textContent`));

  // ------------------------------------------- tolerance: page == API (T6)
  await evalJs(`(() => {
      document.querySelector('#s_protein').value = 40;
      document.querySelector('#s_budget').value = 45;
      document.querySelector('#s_fat').value = '';
      document.querySelector('#s_tolerance').value = '3';
      document.querySelector('#s_max_mains').value = '2';
      document.querySelector('#s-plan').click();
    })()`);
  await waitFor(`S && S.meal === 'lunch' && S.cost > 0 && S.saver`, "容差方案");
  const pageMain = await evalJs(`JSON.stringify({
      items: S.items.map(i => i.code + ':' + i.quantity),
      cost: S.cost, status: S.protein_status, saver: !!S.saver
    })`).then(JSON.parse);
  const apiResp = await fetch(new URL('/api/meal', URL_), {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ meal: 'lunch', protein_min: 40, budget: 45,
                           max_mains: 2, allow_side: false, protein_tolerance: 3 }),
  }).then((r) => r.json());
  const apiMain = {
    items: apiResp.items.map((i) => i.code + ':' + i.quantity),
    cost: apiResp.cost, status: apiResp.protein_status, saver: !!apiResp.saver,
  };
  check("T6 网页与接口主方案一致",
        JSON.stringify(pageMain) === JSON.stringify(apiMain),
        `${JSON.stringify(pageMain)} vs ${JSON.stringify(apiMain)}`);

  const saverBefore = await evalJs(`S.saver ? JSON.stringify(S.saver) : ""`).then((t) => t && JSON.parse(t));
  if (saverBefore) {
    const oldSig = await evalJs(`SSIG`);
    await evalJs(`document.querySelector('#s-saver').click()`);
    await waitFor(`S && S.selected === 'saver'`, "切换到省钱方案");
    const after = await evalJs(`JSON.stringify({
        selected: S.selected, cost: S.cost, status: S.protein_status,
        quotes: S.quotes || {}, sig: SSIG
      })`).then(JSON.parse);
    check("省钱选项切换后成为主方案",
          after.selected === 'saver' && after.status === 'near' && !after.quotes.lunch,
          JSON.stringify(after));
    check("切换后旧报价失效（签名已变）", after.sig !== oldSig,
          `${oldSig} -> ${after.sig}`);
    const stale = await fetch(new URL('/api/quote', URL_), {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ signature: oldSig }),
    });
    check("旧签名报价返回 409", stale.status === 409, String(stale.status));
  } else {
    check("离线样例应带省钱选项", false, "saver missing");
  }

  await sleep(400);
  try { ws.close(); } catch {}
  try { proc.kill("SIGKILL"); } catch {}

  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
  if (failed.length) {
    for (const f of failed) console.log("FAILED:", f.name, f.detail);
    process.exitCode = 1;
  }
}

main().catch((e) => { console.error("FATAL", e); process.exit(2); });
