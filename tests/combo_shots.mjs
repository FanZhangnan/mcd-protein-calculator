// Combo acceptance shots on the *live* service: three scenarios × desktop/mobile.
// Usage: node tests/combo_shots.mjs [url] [outDir]
import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";

const URL_ = process.argv[2] || "http://127.0.0.1:8953/";
const OUT = process.argv[3] || new URL("../docs/screenshots/", import.meta.url).pathname;
const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PORT = 9347;

const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok });
  console.log(`${ok ? "PASS" : "FAIL"} · ${name}${detail ? " · " + detail : ""}`);
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const SCENARIOS = [
  { id: "lunch-44g-45yuan", meal: "lunch", protein: 44, budget: 45 },
  { id: "lunch-35g-25yuan", meal: "lunch", protein: 35, budget: 25 },
  { id: "breakfast-25g-20yuan", meal: "breakfast", protein: 25, budget: 20 },
];

async function main() {
  mkdirSync(OUT, { recursive: true });
  const proc = spawn(CHROME, [
    "--headless=new", "--disable-gpu", "--no-sandbox", "--no-first-run", "--no-proxy-server",
    `--remote-debugging-port=${PORT}`, "--user-data-dir=/tmp/chrome-cdp-combo", "about:blank",
  ], { stdio: "ignore" });
  await sleep(2500);

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
  const evalJs = async (expr) => {
    const r = await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true });
    if (r.result?.exceptionDetails) {
      console.log("JS ERROR", r.result.exceptionDetails.text,
        r.result.exceptionDetails.exception?.description || "");
    }
    return r.result?.result?.value;
  };
  const waitFor = async (expr, label, timeout = 60000) => {
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
  await send("Emulation.setDeviceMetricsOverride", {
    width: 1280, height: 1100, deviceScaleFactor: 1, mobile: false,
  });
  await send("Page.navigate", { url: URL_ });
  await waitFor(`document.querySelector('#meta') && document.querySelector('#meta').textContent.includes('门店')`,
    "实时页面加载");

  const setInputs = async (meal, protein, budget) => {
    await evalJs(`(() => {
      window.__sig = (typeof S !== 'undefined' && S && S.signature) || '';
      const mealBtn = document.querySelector('[data-meal="${meal}"]');
      if (mealBtn) mealBtn.click();
      document.querySelector('#s_protein').value = '${protein}';
      document.querySelector('#s_budget').value = '${budget}';
    })()`);
    await sleep(300);
    await evalJs(`document.querySelector('#s-plan').click()`);
    // wait for *this* request, not for "a result card exists"
    await waitFor(`(typeof S !== 'undefined') && S && S.targets
      && Math.abs(Number(S.targets.protein_min) - ${protein}) < 0.01
      && Math.abs(Number(S.targets.budget) - ${budget}) < 0.01
      && S.signature !== window.__sig
      && !document.querySelector('#s-result-panel').hidden`, "新结果", 90000);
    await sleep(600);
  };

  for (const s of SCENARIOS) {
    await send("Emulation.setDeviceMetricsOverride", {
      width: 1280, height: 1100, deviceScaleFactor: 1, mobile: false,
    });
    await setInputs(s.meal, s.protein, s.budget);
    const state = await evalJs(`JSON.stringify({
      status: S.protein_status, protein: S.totals.protein, cost: S.cost,
      effective: S.effective_cost, money_basis: S.money_basis,
      items: S.items.map(i => i.name + '×' + i.quantity),
      combo: !!(S.combos && S.combos.applied),
      rounds: S.items.filter(i => i.kind === 'combo').map(i => (i.combo.rounds || []).map(r => r.role + '：' + r.name).join(' · ')),
      quote: (S.quotes && S.quotes['${s.meal}'] && S.quotes['${s.meal}'].status) || null
    })`).then(JSON.parse);
    check(`${s.id} 结果`, state.protein !== null && state.cost > 0, JSON.stringify(state));
    const image = await send("Page.captureScreenshot", { format: "png" });
    writeFileSync(`${OUT}/combo-${s.id}-desktop.png`, Buffer.from(image.result.data, "base64"));

    await send("Emulation.setDeviceMetricsOverride", {
      width: 390, height: 844, deviceScaleFactor: 2, mobile: true,
    });
    await sleep(600);
    const scroll = await evalJs(`document.documentElement.scrollWidth - window.innerWidth`);
    check(`${s.id} 手机无横向滚动`, scroll <= 1, `scroll=${scroll}`);
    // 手机截图对准结果卡，套餐各轮与替换说明才看得见
    await evalJs(`document.querySelector('#s-result').scrollIntoView({block: 'start'})`);
    await sleep(400);
    const mobile = await send("Page.captureScreenshot", { format: "png" });
    writeFileSync(`${OUT}/combo-${s.id}-mobile.png`, Buffer.from(mobile.result.data, "base64"));
  }

  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} passed`);
  ws.close();
  proc.kill();
  if (failed.length) process.exitCode = 1;
}

main().catch((e) => { console.error(e); process.exit(1); });
