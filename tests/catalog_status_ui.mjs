// 套餐目录状态：页头徽标 + 门店与数据说明 + 缺目录时的结果卡提示
// Usage: node tests/catalog_status_ui.mjs [url]
import { spawn } from "node:child_process";
import { readFileSync } from "node:fs";

const URL_ = process.argv[2] || "http://127.0.0.1:8765/";
const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PORT = 9349;
// 页面上的套餐数字必须等于构建产物，不能是别处算出来的
const CATALOG = JSON.parse(readFileSync(new URL("../data/combo_catalog.json", import.meta.url), "utf8"));

const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok });
  console.log(`${ok ? "PASS" : "FAIL"} · ${name}${detail ? " · " + detail : ""}`);
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const proc = spawn(CHROME, [
    "--headless=new", "--disable-gpu", "--no-sandbox", "--no-first-run", "--no-proxy-server",
    `--remote-debugging-port=${PORT}`, "--user-data-dir=/tmp/chrome-cdp-catalog", "about:blank",
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
    width: 1280, height: 1000, deviceScaleFactor: 1, mobile: false,
  });
  await send("Page.navigate", { url: URL_ });
  await waitFor(`document.querySelector('#meta') && document.querySelector('#meta').textContent.includes('门店')`,
    "页面加载");

  const badge = await evalJs(`document.querySelector('#data-source').textContent.trim()`);
  const tip = await evalJs(`document.querySelector('#data-source').getAttribute('title') || ''`);
  check("页头徽标写明套餐目录状态", badge.includes("套餐目录："), badge.slice(0, 160));
  const loaded = badge.includes("套餐目录：已加载");
  const meal = await evalJs(`document.querySelector('#s_meal') ? document.querySelector('#s_meal').value : 'lunch'`);
  const entry = CATALOG.meals[meal] || (meal === "dinner" ? CATALOG.meals.lunch : null);
  const reasons = entry ? entry.excluded.reduce((acc, e) => {
    const key = e.reason || "other";
    acc[key] = (acc[key] || 0) + 1;
    return acc;
  }, {}) : {};
  if (loaded) {
    check("已加载时给出组合数与生成时间", /已加载（\d+ 个组合 \/ \d+ 个可用变体，生成于 /.test(tip),
      tip.slice(0, 160));
    if (entry) {
      check("徽标组合数等于 combo_catalog.json",
        badge.includes(`已加载 ${entry.combo_count} 个组合`)
          && tip.includes(`${entry.variants.length} 个可用变体`),
        (badge + " | " + tip).slice(0, 200));
    }
  } else {
    check("未加载时明确说明结果不含套餐", badge.includes("套餐目录：未加载，本次结果不含套餐"),
      badge.slice(0, 160));
  }

  const meta = await evalJs(`document.querySelector('#meta').textContent`);
  check("门店与数据说明里有套餐目录一行", meta.includes("套餐目录"), meta.replace(/\s+/g, " ").slice(0, 160));

  await waitFor(`!document.querySelector('#s-result-panel').hidden`, "结果卡");
  const card = await evalJs(`document.querySelector('#s-result').textContent`);
  if (loaded) {
    check("已加载时结果卡不显示缺目录提示", !card.includes("套餐目录未加载"), "");
    check("已加载时结果卡显示套餐核查入口", card.includes("套餐核查"), "");
    if (entry) {
      // 结果卡底部那一行：已查 / 识别 / 纳入 / 排除（会员价、营养未知）
      const row = card.match(/已查 (\d+) 个 SKU，识别 (\d+) 个套餐，纳入 (\d+) 个变体；排除 (\d+) 个/);
      check("套餐核查数字等于 combo_catalog.json",
        !!row
          && Number(row[1]) === entry.checked
          && Number(row[2]) === entry.combo_count
          && Number(row[3]) === entry.variants.length
          && Number(row[4]) === entry.excluded.length
          && card.includes(`会员价 ${reasons.membership || 0}`)
          && card.includes(`营养未知 ${reasons.nutrition_unknown || 0}`),
        (row ? row[0] : card.replace(/\s+/g, " ").slice(0, 120)));
    }
  } else {
    check("未加载时结果卡顶部有提示", card.includes("套餐目录未加载，本次结果不含套餐"), "");
  }

  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} passed`);
  ws.close();
  proc.kill();
  if (failed.length) process.exitCode = 1;
}

main().catch((e) => { console.error(e); process.exit(1); });
