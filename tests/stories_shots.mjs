// 用户故事 UI 走查 + 截图（实时服务 8780）。
// Usage: node tests/stories_shots.mjs [url]
//
// 需要**实时**服务（不加 --offline），截图与 fixture 取自真实门店菜单：
//
//   MAI_MAI_DATA=/tmp/mai-stories node src/server.py --port 8780
//   node tests/stories_shots.mjs http://127.0.0.1:8780/
//
// S1 预算紧的大学生 / S2 身体数据估算 / S3 赶时间的早餐 /
// S4 在意脂肪 / 移动端 390px。
import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { waitForJson } from "./cdp_ready.mjs";

const URL_ = process.argv[2] || "http://127.0.0.1:8780/";
const OUT = new URL("../data/fixtures/stories/", import.meta.url).pathname;
const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PORT = 9337;

const CN = new Date(Date.now() + 8 * 3600 * 1000);
CN.setUTCDate(CN.getUTCDate() + 1);
const DATE = CN.toISOString().slice(0, 10);

const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"} · ${name}${detail ? " · " + detail : ""}`);
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  mkdirSync(OUT, { recursive: true });
  // deterministic context: same store/date/slots as the live acceptance run
  const post = (path, body) => fetch(new URL(path, URL_), {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then((r) => r.json());
  await post("/api/settings", {
    date: DATE, store: { code: "1440054" },
    meal_times: { breakfast: "08:15", lunch: "12:15", dinner: "18:30" },
  });
  const refreshed = await post("/api/refresh", {});
  check("刷新菜单来源实时", refreshed.source === "live", String(refreshed.source));
  const proc = spawn(CHROME, [
    "--headless=new", "--disable-gpu", "--no-sandbox", "--no-first-run", "--no-proxy-server",
    `--remote-debugging-port=${PORT}`, "--user-data-dir=/tmp/chrome-cdp-stories",
    "about:blank",
  ], { stdio: "ignore" });
  await sleep(2500);

  const tab = await waitForJson(`http://127.0.0.1:${PORT}/json/new?${encodeURIComponent(URL_)}`,
    { method: "PUT" });
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
  const evalJs = async (expr) => {
    const r = await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true });
    if (r.result?.exceptionDetails) {
      console.log("JS ERROR", r.result.exceptionDetails.text,
        r.result.exceptionDetails.exception?.description || "");
    }
    return r.result?.result?.value;
  };
  const waitFor = async (expr, label, timeout = 120000) => {
    const t0 = Date.now();
    while (Date.now() - t0 < timeout) {
      if (await evalJs(expr)) return true;
      await sleep(400);
    }
    check(`等待超时：${label}`, false, expr.slice(0, 80));
    return false;
  };

  await send("Page.enable");
  await send("Runtime.enable");
  await send("Emulation.setDeviceMetricsOverride", {
    width: 1280, height: 1000, deviceScaleFactor: 1, mobile: false,
  });
  await send("Page.navigate", { url: URL_ });
  await waitFor(`document.querySelector('#meta') && document.querySelector('#meta').textContent.includes('门店')`, "页面加载");
  check("数据来源显示实时",
        (await evalJs(`document.querySelector('#meta').textContent.includes('实时')`)) === true);

  const shot = async (name) => {
    const metrics = await send("Page.getLayoutMetrics");
    const height = Math.ceil(metrics.result.cssContentSize.height);
    const image = await send("Page.captureScreenshot", {
      format: "png", captureBeyondViewport: true,
      clip: { x: 0, y: 0, width: 1280, height, scale: 1 },
    });
    writeFileSync(`${OUT}${name}.png`, Buffer.from(image.result.data, "base64"));
  };

  const plan = async (meal, protein, budget, fat = "", maxMains = 2) => {
    await evalJs(`(() => {
        document.querySelector('#s_meal').value = '${meal}';
        document.querySelector('#s_protein').value = '${protein}';
        document.querySelector('#s_budget').value = '${budget}';
        document.querySelector('#s_fat').value = '${fat}';
        document.querySelector('#s_max_mains').value = '${maxMains}';
        document.querySelector('#s_side').checked = false;
        document.querySelector('#s_protein').dispatchEvent(new Event('input'));
        document.querySelector('#s_budget').dispatchEvent(new Event('input'));
        document.querySelector('#s_fat').dispatchEvent(new Event('input'));
        document.querySelector('#s_meal').dispatchEvent(new Event('change'));
      })()`);
    await waitFor(`LAND && LAND.meal === '${meal}'`, "性价比图切换");
    await evalJs(`S = null; document.querySelector('#s-plan').click()`);
    await waitFor(`S && S.meal === '${meal}' && typeof S.items !== 'undefined'`, "方案返回");
    await sleep(300);
  };

  // ------------------------------------------------------------- story 1
  await plan("lunch", 35, 25);
  const s1 = await evalJs(`JSON.stringify({
      feasible: S.feasible, cost: S.cost, protein: S.totals.protein,
      items: S.items.map(i => i.name + '×' + i.quantity),
      text: document.querySelector('#s-result').textContent,
      status: document.querySelector('#s-status').textContent,
      region: (() => { const r = document.querySelector('#chart rect.region');
        return r ? {x: +r.getAttribute('width'), y: +r.getAttribute('height')} : null; })(),
      card: (() => {
        const el = document.querySelector('#chart .rec-marker');
        return el ? {cx: +el.dataset.x, cy: +el.dataset.y} : null;
      })()
    })`).then(JSON.parse);
  check("故事1 无解且主卡在预算内",
        s1.feasible === false && s1.cost <= 25, `¥${s1.cost} P${s1.protein}`);
  check("故事1 显示预算内最多的说明与加预算卡片",
        s1.text.includes("最多只能做到") && s1.text.includes("加预算到"),
        s1.text.slice(0, 90));
  check("故事1 卡片点位落在达标区右边界外",
        s1.region && s1.card && Math.abs(s1.card.cx - (56 + s1.region.x)) < 1.5,
        JSON.stringify({region: s1.region, card: s1.card}));
  check("故事1 卡片点位低于蛋白质达标线",
        s1.region && s1.card && s1.card.cy > (18 + s1.region.y) - 1,
        JSON.stringify({card: s1.card, region: s1.region}));
  const s1relax = await evalJs(`JSON.stringify([...document.querySelectorAll('#s-result .under-card')]
      .map(e => e.textContent))`).then(JSON.parse);
  check("故事1 加预算卡片写出完整组合",
        s1relax.some((t) => t.includes("39.00") && t.includes("双层吉士汉堡 + 麦香鸡")),
        JSON.stringify(s1relax));
  await shot("story1_lunch_35_25");

  // ------------------------------------------------------------- story 2
  await evalJs(`document.querySelector('#s_sex').closest('details').open = true`);
  await evalJs(`document.querySelector('#s_meal').value = 'dinner';
                document.querySelector('#s_meal').dispatchEvent(new Event('change'))`);
  await waitFor(`LAND && LAND.meal === 'dinner'`, "晚餐性价比图");
  await evalJs(`(() => {
      document.querySelector('#s_budget').value = 45;
      document.querySelector('#s_sex').value = 'male';
      document.querySelector('#s_age').value = 28;
      document.querySelector('#s_height').value = 178;
      document.querySelector('#s_weight').value = 80;
      document.querySelector('#s_meals').value = 3;
      document.querySelector('#s_goal').value = 'muscle';
      document.querySelector('#s_activity').value = '1.55';
      document.querySelector('#s-calc').click();
    })()`);
  await sleep(400);
  const s2 = await evalJs(`JSON.stringify({
      protein: document.querySelector('#s_protein').value,
      hint: document.querySelector('#s-cap-hint').textContent,
      note: document.querySelector('#s-calc-note').textContent,
      kcalMax: document.querySelector('#s_kcal_max').value,
      kcalMin: document.querySelector('#s_kcal_min').value
    })`).then(JSON.parse);
  check("故事2 预填蛋白质 53 g", s2.protein === "53", s2.protein);
  check("故事2 TDEE 标注为参考",
        s2.note.includes("TDEE") && s2.note.includes("热量仅为参考"),
        s2.note.slice(0, 100));
  check("故事2 热量未预填为约束", s2.kcalMax === "" && s2.kcalMin === "",
        JSON.stringify([s2.kcalMin, s2.kcalMax]));
  check("故事2 提示 45 元最多 54 g", s2.hint.includes("54") && s2.hint.includes("最多能买到"),
        s2.hint);
  await evalJs(`S = null; document.querySelector('#s-plan').click()`);
  await waitFor(`S && S.meal === 'dinner' && S.items`, "晚餐方案");
  const s2b = await evalJs(`JSON.stringify({
      feasible: S.feasible, cost: S.cost, protein: S.totals.protein, fat: S.totals.fat,
      items: S.items.map(i => i.name + '×' + i.quantity)
    })`).then(JSON.parse);
  check("故事2 求解与提示一致（54 g / 45 元）",
        s2b.feasible === true && s2b.protein === 54 && s2b.cost === 45,
        JSON.stringify(s2b));
  await shot("story2_body_dinner_45");

  // ------------------------------------------------------------- story 3
  await plan("breakfast", 25, 20);
  const s3 = await evalJs(`JSON.stringify({
      items: S.items.map(i => ({name: i.name, code: String(i.code)})),
      feasible: S.feasible, cost: S.cost, protein: S.totals.protein,
      land: LAND.items.map(i => String(i.code))
    })`).then(JSON.parse);
  check("故事3 只出现早餐商品",
        s3.items.every((i) => s3.land.includes(i.code)), JSON.stringify(s3.items));
  check("故事3 达标",
        s3.feasible === true && s3.protein >= 25 && s3.cost <= 20,
        `P${s3.protein} ¥${s3.cost}`);
  check("故事3 手改目标后上一故事的估算说明已清空",
        (await evalJs(`document.querySelector('#s-calc-note').textContent`)) === "",
        await evalJs(`document.querySelector('#s-calc-note').textContent`));
  await evalJs(`document.querySelector('#s-quote').click()`);
  await waitFor(`S && S.quotes && S.quotes.breakfast && S.quotes.breakfast.status`, "早餐报价");
  const s3q = await evalJs(`JSON.stringify({
      status: document.querySelector('#s-status').textContent,
      reservation: S.quotes.breakfast.reservation_date,
      amount: S.quotes.breakfast.amount
    })`).then(JSON.parse);
  check("故事3 报价带早餐时点",
        s3q.reservation === `${DATE} 08:15`, String(s3q.reservation));
  check("故事3 页面显示试算成功", s3q.status.includes("试算成功"), s3q.status.slice(0, 60));
  await shot("story3_breakfast_25_20");

  // ------------------------------------------------------------- story 4
  await evalJs(`document.querySelector('#s_fat').closest('details').open = true`);
  await plan("lunch", 40, 45, 30);
  const s4 = await evalJs(`JSON.stringify({
      feasible: S.feasible, fat: S.totals.fat, cost: S.cost, protein: S.totals.protein,
      text: document.querySelector('#s-result').textContent,
      targets: S.suggestions.map(o => ({name: o.name, warn: o.muscle_warning, risk: o.protein_risk,
                                         target: o.target_name}))
    })`).then(JSON.parse);
  check("故事4 主卡脂肪 ≤30", s4.fat <= 30, `F${s4.fat}`);
  const relax = await evalJs(`JSON.stringify([...document.querySelectorAll('#s-result .relax-card')]
      .map(e => e.textContent))`).then(JSON.parse);
  check("故事4 显示放宽脂肪数值",
        relax.some((t) => t.includes("放宽脂肪") && t.includes("35")),
        JSON.stringify(relax));
  check("故事4 放宽脂肪写出完整组合",
        relax.some((t) => t.includes("35") && t.includes("双层吉士汉堡 + 麦香鱼")),
        JSON.stringify(relax));
  check("故事4 手填蛋白质后旧的估算说明已清空",
        (await evalJs(`document.querySelector('#s-calc-note').textContent`)) === "",
        await evalJs(`document.querySelector('#s-calc-note').textContent`));
  const cheese = s4.targets.find((t) => t.name.includes("吉士") || t.name.includes("芝士"));
  check("故事4 芝士项带增肌提示",
        !!cheese && cheese.warn === true && cheese.risk === true, JSON.stringify(cheese));

  const before = await evalJs(`JSON.stringify({kcal: S.totals.energyKcal, p: S.totals.protein})`);
  await evalJs(`(() => {
      const d = document.querySelector('details.sugg-box');
      if (d) d.open = true;
      const labels = [...document.querySelectorAll('label.sugg')];
      const el = labels.find(l => l.textContent.includes('吉士') || l.textContent.includes('芝士'));
      const box = el && el.querySelector('input.srem');
      if (!box) return false;
      box.checked = true;
      box.dispatchEvent(new Event('change', {bubbles: true}));
      return true;
    })()`);
  await waitFor(`S && S.items.some(i => i.nutrition_basis === 'standard_unadjusted')`, "去芝士重算");
  const s4b = await evalJs(`JSON.stringify({
      after: {kcal: S.totals.energyKcal, p: S.totals.protein},
      notes: S.items.map(i => i.removal_notes).flat(),
      text: document.querySelector('#s-result').textContent
    })`).then(JSON.parse);
  check("故事4 去芝士后营养不变", s4b.after.kcal === JSON.parse(before).kcal,
        JSON.stringify(s4b.after));
  check("故事4 页面展示严格模式说明",
        s4b.notes.some((n) => n.includes("标准版数值"))
        && s4b.notes.some((n) => n.includes("蛋白质可能低于显示值")),
        JSON.stringify(s4b.notes));
  await shot("story4_lunch_40_45_fat30_cheese");

  // ------------------------------------------------------------- mobile
  await send("Emulation.setDeviceMetricsOverride", {
    width: 390, height: 844, deviceScaleFactor: 2, mobile: true,
  });
  await send("Page.navigate", { url: URL_ });
  await waitFor(`document.querySelector('#meta') && document.querySelector('#meta').textContent.includes('门店')`, "移动端加载");
  await waitFor(`typeof LAND !== 'undefined' && LAND && LAND.items.length > 0`, "移动端图");
  const mobile = await evalJs(`JSON.stringify({
      scrollW: document.documentElement.scrollWidth,
      clientW: document.documentElement.clientWidth,
      chartW: document.querySelector('#chart').getBoundingClientRect().width,
      chartRight: document.querySelector('#chart').getBoundingClientRect().right,
      tableW: document.querySelector('#rank-table').getBoundingClientRect().width
    })`).then(JSON.parse);
  check("移动端 390 无横向滚动",
        mobile.scrollW <= mobile.clientW + 1,
        JSON.stringify({scrollW: mobile.scrollW, clientW: mobile.clientW}));
  check("移动端散点图不溢出",
        mobile.chartRight <= mobile.clientW + 1 && mobile.chartW <= mobile.clientW,
        JSON.stringify({chartW: mobile.chartW, right: mobile.chartRight}));
  await evalJs(`(() => {
      document.querySelector('#s_protein').value = 25;
      document.querySelector('#s_budget').value = 20;
      document.querySelector('#s_meal').value = 'breakfast';
      document.querySelector('#s_meal').dispatchEvent(new Event('change'));
    })()`);
  await waitFor(`LAND && LAND.meal === 'breakfast'`, "移动端早餐图");
  await evalJs(`document.querySelector('#s-plan').click()`);
  await waitFor(`S && S.items && S.items.length > 0`, "移动端方案");
  const m2 = await evalJs(`JSON.stringify({
      scrollW: document.documentElement.scrollWidth,
      clientW: document.documentElement.clientWidth
    })`).then(JSON.parse);
  check("移动端结果卡也不溢出", m2.scrollW <= m2.clientW + 1, JSON.stringify(m2));
  const metrics = await send("Page.getLayoutMetrics");
  const mh = Math.ceil(metrics.result.cssContentSize.height);
  const img = await send("Page.captureScreenshot", {
    format: "png", captureBeyondViewport: true,
    clip: { x: 0, y: 0, width: 390, height: mh, scale: 2 },
  });
  writeFileSync(`${OUT}boundary_mobile_390.png`, Buffer.from(img.result.data, "base64"));

  await sleep(300);
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
