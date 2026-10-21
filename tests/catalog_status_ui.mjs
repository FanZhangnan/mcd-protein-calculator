// 套餐目录状态：页头徽标 + 门店与数据说明 + 缺目录时的结果卡提示
// Usage: node tests/catalog_status_ui.mjs [url]
import { spawn } from "node:child_process";

const URL_ = process.argv[2] || "http://127.0.0.1:8765/";
const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PORT = 9349;

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
  if (loaded) {
    check("已加载时给出组合数与生成时间", /已加载（\d+ 个组合 \/ \d+ 个可用变体，生成于 /.test(tip),
      tip.slice(0, 160));
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
