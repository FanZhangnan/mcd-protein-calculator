// Static demo acceptance: three scenarios, no backend calls, desktop + mobile shots.
// Usage: node tests/demo_ui.mjs [url] [outDir]
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";

const URL_ = process.argv[2] || "http://127.0.0.1:8941/";
const OUT = process.argv[3] || new URL("../docs/screenshots/", import.meta.url).pathname;
const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PORT = 9341;
// 套餐核查那一行的唯一口径：构建脚本写下的套餐目录
const CATALOG = JSON.parse(readFileSync(new URL("../data/combo_catalog.json", import.meta.url), "utf8"));
// 多人分享套餐清单：这些套餐不参与单人推荐（文件为空时不做限制）
const SHARED_FILE = new URL("../data/shared_combos.json", import.meta.url);
const SHARED_NAMES = existsSync(SHARED_FILE)
  ? Object.values(JSON.parse(readFileSync(SHARED_FILE, "utf8")).combos || {}).map((x) => x.name)
  : [];
const LUNCH = CATALOG.meals.lunch;
const LUNCH_USABLE = new Set(LUNCH.variants.map((v) => v.combo_code));
const LUNCH_EXCLUDED = new Set(LUNCH.excluded
  .map((e) => e.combo_code)
  .filter((code) => code && !LUNCH_USABLE.has(code)));
const LUNCH_REASONS = LUNCH.excluded.reduce((acc, e) => {
  if (!e.combo_code || LUNCH_USABLE.has(e.combo_code)) return acc;
  const key = e.reason || "other";
  (acc[key] = acc[key] || new Set()).add(e.combo_code);
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
  // 同款套餐可以点 2 份：54 g / 29.80（人气经典随心配×2）。
  // 旧值「随心配 + 双层吉士」37.40 元，新方案更便宜且蛋白相同。
  check("场景1 达标 54 g / 29.80 元",
    c1.status === "met" && c1.protein === 54 && c1.cost === 29.8
    && c1.items.join(" + ") === "人气经典随心配×2", JSON.stringify(c1.items));
  // 便宜 1.00 元（29.80 → 28.80）不到 2 元：省钱卡片不显示
  check("场景1 省不到 2 元不显示省钱选项", c1.saver === null, JSON.stringify(c1.saver));
  // 套餐必须在结果卡里展开各轮内容（小食 / 饮品分别是什么）。
  // 之前 demo 桩漏传 combo 字段，页面上只剩「套餐」两个字。
  const c1parts = await evalJs(`JSON.stringify({
    parts: (document.querySelector('.combo-parts') || {}).textContent || '',
    replace: (document.querySelector('.combo-replace') || {}).textContent || '',
    rounds: (S.items.find(i => i.kind === 'combo') || {}).combo?.rounds || [],
    audit: (document.querySelector('.combo-audit summary') || {}).textContent || ''
  })`).then(JSON.parse);
  // 套餐必须在结果卡里展开各轮内容。随心配的轮次是「蓝区 / 粉区」，
  // 必须按区打印，否则用户不知道每个区该点哪个（旧值：主食：… · 饮品：…）。
  check("场景1 套餐按区列出各轮内容",
    c1parts.parts.includes("蓝区：双层吉士汉堡") && c1parts.parts.includes("粉区：无糖可口可乐中杯"),
    c1parts.parts.slice(0, 80));
  check("场景1 替换说明写明粉区改成了什么",
    c1parts.replace.includes("粉区：可乐麦炫酷 → 无糖可口可乐中杯"),
    c1parts.replace.slice(0, 100));
  check("场景1 套餐各轮数据在方案里",
    c1parts.rounds.length >= 2 && c1parts.rounds.every(r => r.name && r.role),
    JSON.stringify(c1parts.rounds.map(r => r.role + "：" + r.name)));
  check("场景1 套餐核查写出真实 SKU 数",
    /已查 \d+ 个 SKU/.test(c1parts.audit) && !/已查 11 个 SKU/.test(c1parts.audit),
    c1parts.audit.slice(0, 90));
  // 这一行的数字必须等于 data/combo_catalog.json；套餐数与变体数分开写。
  const auditRow = c1parts.audit.match(
    /已查 (\d+) 个 SKU，识别 (\d+) 个套餐；可用 (\d+) 个（(\d+) 个变体），排除 (\d+) 个/);
  check("场景1 套餐核查与 combo_catalog.json 一致",
    !!auditRow
      && Number(auditRow[1]) === LUNCH.checked
      && Number(auditRow[2]) === LUNCH.combo_count
      && Number(auditRow[3]) === LUNCH_USABLE.size
      && Number(auditRow[4]) === LUNCH.variants.length
      && Number(auditRow[5]) === LUNCH_EXCLUDED.size
      && LUNCH_USABLE.size + LUNCH_EXCLUDED.size === LUNCH.combo_count
      && c1parts.audit.includes(`会员价 ${(LUNCH_REASONS.membership || new Set()).size}`)
      && c1parts.audit.includes(`营养未知 ${(LUNCH_REASONS.nutrition_unknown || new Set()).size}`),
    c1parts.audit.slice(0, 160));
  // 替换必须写在结果卡上：粉区 可乐麦炫酷 → 无糖可口可乐中杯
  const replaceText = await evalJs(`(document.querySelector('.combo-replace') || {}).textContent || ''`);
  check("场景1 结果卡写明本轮替换",
    replaceText.includes("可乐麦炫酷") && replaceText.includes("无糖可口可乐中杯")
      && replaceText.includes("原选项暂无官方营养"), replaceText.slice(0, 120));
  const badge = await evalJs(`JSON.stringify({
    text: document.querySelector('#data-source').textContent,
    title: document.querySelector('#data-source').getAttribute('title') || ''
  })`).then(JSON.parse);
  check("页头徽标组合数与目录一致",
    badge.text.includes(`已加载 ${LUNCH.combo_count} 个组合`)
      && badge.title.includes(`${LUNCH.variants.length} 个可用变体`),
    (badge.text + " | " + badge.title).slice(0, 200));
  const unknownText = await evalJs(`document.querySelector('#unknown').textContent`);
  check("在线页门店说明列出暂无官方营养的商品",
    unknownText.includes("以下商品暂无官方营养数据，暂不参与计算")
      && unknownText.includes("苹果片一袋(60g)")
      && unknownText.includes("“苹板”支撑Pro套餐"),
    unknownText.replace(/\s+/g, " ").slice(0, 160));
  await shot("demo-desktop", 1280);

  // 2) lunch 35 / 25 → under, then the budget card
  await setInputs("lunch", 35, 25);
  const c2 = await evalJs(`JSON.stringify({
    status: S.protein_status, cost: S.cost, protein: S.totals.protein,
    text: document.querySelector('#s-result').textContent
  })`).then(JSON.parse);
  check("场景2 预算内最多 28 g / 25.00 元",
    c2.status === "under" && c2.protein === 28 && c2.cost === 25, JSON.stringify(c2));
  // 两份套餐：加预算 28.80 元 → 42 g（人气经典 + 精选超值）
  check("场景2 显示加预算卡片", c2.text.includes("加预算到") && c2.text.includes("28.80"), "");
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
  check("场景2 按 28.80 元重算后达标",
    clicked === true && Number(c2b.budget) === 28.8 && c2b.status === "met"
    && c2b.protein === 42 && c2b.cost === 28.8, JSON.stringify(c2b));

  // 3) breakfast 25 / 20
  await setInputs("breakfast", 25, 20);
  const c3 = await evalJs(`JSON.stringify({
    status: S.protein_status, cost: S.cost, protein: S.totals.protein
  })`).then(JSON.parse);
  check("场景3 早餐达标",
    c3.status === "met" && c3.protein >= 25 && c3.cost <= 20, JSON.stringify(c3));

  // 5) 高目标 80 g / 100 元：不能出现多人餐（堡堡随心拼 / 薄皮V翅 自带 2 个主食）
  await setInputs("lunch", 80, 100);
  const c5 = await evalJs(`JSON.stringify({
    status: S.protein_status, protein: S.totals.protein, cost: S.cost,
    items: S.items.map(i => i.name + '×' + i.quantity),
    mains: S.items.reduce((n, i) => n + (i.combo ? (i.combo.mains || 1) : 1) * i.quantity, 0),
    saver: S.saver ? { cost: S.saver.cost, protein: S.saver.protein, saved: S.saver.money_saved } : null
  })`).then(JSON.parse);
  // 多人分享套餐清单（data/shared_combos.json）里的套餐不能出现在推荐里
  check("场景5 高目标不含多人分享套餐",
    !c5.items.some((n) => SHARED_NAMES.some((x) => n.includes(x))), JSON.stringify(c5.items));
  check("场景5 主食数不超过上限", c5.mains <= 2, `mains=${c5.mains}`);
  // 具体数字取决于清单是否生效：清单前 89 g / 73.00（堡堡），清单后 77 g / 71.50
  check("场景5 高目标给出可接受的结果",
    ["met", "near"].includes(c5.status) && c5.protein >= 77 && c5.cost <= 73, JSON.stringify(c5));
  check("场景5 省不到 2 元不显示省钱选项", c5.saver === null, JSON.stringify(c5.saver));

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
  check("手机结果正确", m.status === "met" && m.protein === 54, JSON.stringify(m));
  await shot("demo-mobile", 390);

  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} passed`);
  ws.close();
  proc.kill();
  if (failed.length) process.exitCode = 1;
}

main().catch((err) => { console.error(err); process.exit(2); });
