// Static demo acceptance: three scenarios, no backend calls, desktop + mobile shots.
// Usage: node tests/demo_ui.mjs [url] [outDir]
import { spawn } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";

const URL_ = process.argv[2] || "http://127.0.0.1:8941/";
const OUT = process.argv[3] || new URL("../docs/screenshots/", import.meta.url).pathname;
const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PORT = 9341;
// 套餐核查那一行的唯一口径：构建脚本写下的套餐目录
const CATALOG = JSON.parse(readFileSync(new URL("../data/combo_catalog.json", import.meta.url), "utf8"));
const LUNCH = CATALOG.meals.lunch;
const LUNCH_REASONS = LUNCH.excluded.reduce((acc, e) => {
  const key = e.reason || "other";
  acc[key] = (acc[key] || 0) + 1;
  return acc;
}, {});

const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"} · ${name}${detail ? " · " + detail : ""}`);
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  mkdirSync(OUT, { recursive: true });
  const proc = spawn(CHROME, [
    "--headless=new", "--disable-gpu", "--no-sandbox", "--no-first-run", "--no-proxy-server",
    `--remote-debugging-port=${PORT}`, "--user-data-dir=/tmp/chrome-cdp-demo", "about:blank",
  ], { stdio: "ignore" });
  await sleep(2500);

  const tab = await (await fetch(`http://127.0.0.1:${PORT}/json/new?${encodeURIComponent(URL_)}`,
    { method: "PUT" })).json();
  const ws = new WebSocket(tab.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });

  let id = 0;
  const pending = new Map();
  const requests = [];
  ws.onmessage = (msg) => {
    const data = JSON.parse(msg.data);
    if (data.id && pending.has(data.id)) { pending.get(data.id)(data); pending.delete(data.id); }
    if (data.method === "Network.requestWillBeSent") requests.push(data.params.request.url);
  };
  const send = (method, params = {}) => new Promise((resolve) => {
    const mid = ++id;
    pending.set(mid, resolve);
    ws.send(JSON.stringify({ id: mid, method, params }));
  });
  const evalJs = async (expr) => {
    const r = await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true });
    if (r.result?.exceptionDetails) {
      console.log("JS ERROR", r.result.exceptionDetails.text,
        r.result.exceptionDetails.exception?.description || "");
    }
    return r.result?.result?.value;
  };
  const waitFor = async (expr, label, timeout = 30000) => {
    const t0 = Date.now();
    while (Date.now() - t0 < timeout) {
      if (await evalJs(expr)) return true;
      await sleep(300);
    }
    check(`等待超时：${label}`, false, expr.slice(0, 80));
    return false;
  };

  await send("Page.enable");
  await send("Runtime.enable");
  await send("Network.enable");
  await send("Emulation.setDeviceMetricsOverride", {
    width: 1280, height: 1000, deviceScaleFactor: 1, mobile: false,
  });
  await send("Page.navigate", { url: URL_ });
  await waitFor(`document.querySelector('#meta') && document.querySelector('#meta').textContent.includes('门店')`, "页面加载");

  check("徽标显示快照数据",
    (await evalJs(`document.querySelector('#data-source').getAttribute('data-source')`)) === "snapshot",
    await evalJs(`document.querySelector('#data-source').textContent`));

  const setInputs = async (meal, protein, budget) => {
    await evalJs(`(() => {
      document.querySelector('#s_meal').value = '${meal}';
      document.querySelector('#s_protein').value = '${protein}';
      document.querySelector('#s_budget').value = '${budget}';
      document.querySelector('#s_fat').value = '';
      document.querySelector('#s_max_mains').value = '2';
      document.querySelector('#s_side').checked = false;
      document.querySelector('#s_meal').dispatchEvent(new Event('change'));
    })()`);
    await waitFor(`LAND && LAND.meal === '${meal}'`, "性价比图切换");
    await evalJs(`S = null; document.querySelector('#s-plan').click()`);
    await waitFor(`S && S.meal === '${meal}' && typeof S.items !== 'undefined'`, "方案返回");
    await sleep(500);
  };
  const shot = async (name, width) => {
    const metrics = await send("Page.getLayoutMetrics");
    const height = Math.ceil(metrics.result.cssContentSize.height);
    const image = await send("Page.captureScreenshot", {
      format: "png", captureBeyondViewport: true,
      clip: { x: 0, y: 0, width, height, scale: 1 },
    });
    writeFileSync(`${OUT}${name}.png`, Buffer.from(image.result.data, "base64"));
    return height;
  };

  // 1) lunch 44 / 45
  await setInputs("lunch", 44, 45);
  const c1 = await evalJs(`JSON.stringify({
    status: S.protein_status, cost: S.cost, protein: S.totals.protein,
    items: S.items.map(i => i.name + '×' + i.quantity),
    saver: S.saver ? { cost: S.saver.cost, protein: S.saver.protein, items: S.saver.items } : null
  })`).then(JSON.parse);
  // 套餐纳入后：旧 54 g / 45.00（双层吉士汉堡×2）→ 新 50 g / 40.50
  // （酥脆无双套餐 + 双层吉士汉堡）：更便宜、仍达标。
  check("场景1 达标 50 g / 40.50 元",
    c1.status === "met" && c1.protein === 50 && c1.cost === 40.5
    && c1.items.join(" + ") === "酥脆无双套餐×1 + 双层吉士汉堡×1", JSON.stringify(c1.items));
  check("场景1 有省钱选项 42 g / 39.00 元",
    !!c1.saver && c1.saver.protein === 42 && c1.saver.cost === 39,
    JSON.stringify(c1.saver));
  // 套餐必须在结果卡里展开各轮内容（小食 / 饮品分别是什么）。
  // 之前 demo 桩漏传 combo 字段，页面上只剩「套餐」两个字。
  const c1parts = await evalJs(`JSON.stringify({
    parts: (document.querySelector('.combo-parts') || {}).textContent || '',
    rounds: (S.items.find(i => i.kind === 'combo') || {}).combo?.rounds || [],
    audit: (document.querySelector('.combo-audit summary') || {}).textContent || ''
  })`).then(JSON.parse);
  check("场景1 套餐列出各轮内容",
    c1parts.parts.length > 0 && c1parts.parts.includes("小食") && c1parts.parts.includes("饮品"),
    c1parts.parts.slice(0, 80));
  check("场景1 套餐各轮数据在方案里",
    c1parts.rounds.length >= 2 && c1parts.rounds.every(r => r.name && r.role),
    JSON.stringify(c1parts.rounds.map(r => r.role + "：" + r.name)));
  check("场景1 套餐核查写出真实 SKU 数",
    /已查 \d+ 个 SKU/.test(c1parts.audit) && !/已查 11 个 SKU/.test(c1parts.audit),
    c1parts.audit.slice(0, 90));
  // 这一行的四个数字必须等于 data/combo_catalog.json，而不是浏览器数变体：
  // 午餐识别 50 个套餐、纳入 33 个变体、排除 45 个（会员价 16、营养未知 29）。
  const auditRow = c1parts.audit.match(/已查 (\d+) 个 SKU，识别 (\d+) 个套餐，纳入 (\d+) 个变体；排除 (\d+) 个/);
  check("场景1 套餐核查与 combo_catalog.json 一致",
    !!auditRow
      && Number(auditRow[1]) === LUNCH.checked
      && Number(auditRow[2]) === LUNCH.combo_count
      && Number(auditRow[3]) === LUNCH.variants.length
      && Number(auditRow[4]) === LUNCH.excluded.length
      && c1parts.audit.includes(`会员价 ${LUNCH_REASONS.membership || 0}`)
      && c1parts.audit.includes(`营养未知 ${LUNCH_REASONS.nutrition_unknown || 0}`),
    c1parts.audit.slice(0, 120));
  const badge = await evalJs(`JSON.stringify({
    text: document.querySelector('#data-source').textContent,
    title: document.querySelector('#data-source').getAttribute('title') || ''
  })`).then(JSON.parse);
  check("页头徽标组合数与目录一致",
    badge.text.includes(`已加载 ${LUNCH.combo_count} 个组合`)
      && badge.title.includes(`${LUNCH.variants.length} 个可用变体`),
    (badge.text + " | " + badge.title).slice(0, 200));
  await shot("demo-desktop", 1280);

  // 2) lunch 35 / 25 → under, then the budget card
  await setInputs("lunch", 35, 25);
  const c2 = await evalJs(`JSON.stringify({
    status: S.protein_status, cost: S.cost, protein: S.totals.protein,
    text: document.querySelector('#s-result').textContent
  })`).then(JSON.parse);
  check("场景2 预算内最多 28 g / 25.00 元",
    c2.status === "under" && c2.protein === 28 && c2.cost === 25, JSON.stringify(c2));
  // 套餐让「加预算」的上限从 39.00 降到 32.00（酥脆无双套餐 + 汉堡包 = 36 g）
  check("场景2 显示加预算卡片", c2.text.includes("加预算到") && c2.text.includes("32.00"), "");
  const clicked = await evalJs(`(() => {
    const b = document.querySelector('#s-budget-up');
    if (!b) return false;
    b.click(); return true;
  })()`);
  await sleep(800);
  const c2b = await evalJs(`JSON.stringify({
    budget: document.querySelector('#s_budget').value,
    status: S.protein_status, cost: S.cost, protein: S.totals.protein
  })`).then(JSON.parse);
  check("场景2 按 32 元重算后达标",
    clicked === true && c2b.budget === "32" && c2b.status === "met"
    && c2b.protein === 36 && c2b.cost === 32, JSON.stringify(c2b));

  // 3) breakfast 25 / 20
  await setInputs("breakfast", 25, 20);
  const c3 = await evalJs(`JSON.stringify({
    status: S.protein_status, cost: S.cost, protein: S.totals.protein
  })`).then(JSON.parse);
  check("场景3 早餐达标",
    c3.status === "met" && c3.protein >= 25 && c3.cost <= 20, JSON.stringify(c3));

  // 4) snapshot-only guarantees
  const apiReqs = requests.filter((u) => u.includes("/api/") || u.includes("127.0.0.1"));
  check("无 /api/ 或 127.0.0.1 请求",
    apiReqs.filter((u) => !u.startsWith(URL_)).length === 0, apiReqs.join(", "));
  check("无报价按钮，只有说明",
    (await evalJs(`!!document.querySelector('#s-quote')`)) === false
    && (await evalJs(`document.querySelector('.quote-note') ? document.querySelector('.quote-note').textContent.includes('在线版不连接门店') : false`)) === true);
  check("可去配料只展示、不可勾选",
    (await evalJs(`document.querySelectorAll('input.srem').length`)) === 0,
    await evalJs(`document.querySelector('#s-result').textContent.includes('点单建议') ? '有建议区' : '无建议区'`));

  // 5) mobile 390
  await send("Emulation.setDeviceMetricsOverride", {
    width: 390, height: 844, deviceScaleFactor: 2, mobile: true,
  });
  await send("Page.navigate", { url: URL_ });
  await waitFor(`document.querySelector('#meta') && document.querySelector('#meta').textContent.includes('门店')`, "手机加载");
  await setInputs("lunch", 44, 45);
  const m = await evalJs(`JSON.stringify({
    scroll: document.documentElement.scrollWidth, inner: window.innerWidth,
    status: S.protein_status, protein: S.totals.protein
  })`).then(JSON.parse);
  check("手机 390px 无横向滚动", m.scroll <= m.inner + 1, `scroll=${m.scroll} inner=${m.inner}`);
  check("手机结果正确", m.status === "met" && m.protein === 50, JSON.stringify(m));
  await shot("demo-mobile", 390);

  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} passed`);
  ws.close();
  proc.kill();
  if (failed.length) process.exitCode = 1;
}

main().catch((err) => { console.error(err); process.exit(2); });
