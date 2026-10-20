// 边界 UI 检查：
//   1) 8782 —— 午餐时段设成 10:30，页面必须提示「两个供餐时段之间」
//   2) 8783 —— 离线桩强制报价失败，页面必须显示失败与参考价，不能显示 0 元
// Usage: node tests/boundary_ui.mjs [gapUrl] [failUrl]
import { spawn } from "node:child_process";
import { waitForJson } from "./cdp_ready.mjs";

const GAP = process.argv[2] || "http://127.0.0.1:8782/";
const FAIL = process.argv[3] || "http://127.0.0.1:8783/";
const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PORT = 9338;

const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"} · ${name}${detail ? " · " + detail : ""}`);
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const proc = spawn(CHROME, [
    "--headless=new", "--disable-gpu", "--no-sandbox", "--no-first-run",
    `--remote-debugging-port=${PORT}`, "--user-data-dir=/tmp/chrome-cdp-boundary",
    "about:blank",
  ], { stdio: "ignore" });
  await sleep(2500);

  const tab = await waitForJson(`http://127.0.0.1:${PORT}/json/new?${encodeURIComponent(GAP)}`,
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

  // ---------------------------------------------------------- 10:30 gap
  await send("Page.navigate", { url: GAP });
  await waitFor(`document.querySelector('#meta') && document.querySelector('#meta').textContent.includes('门店')`,
    "时段边界页加载");
  await waitFor(`typeof WARN !== 'undefined' && WARN && WARN.lunch`, "时段提示注入");
  const gapStatus = await evalJs(`document.querySelector('#s-status').textContent`);
  check("10:30 页面提示落在两个供餐时段之间",
        gapStatus.includes("两个供餐时段之间") && gapStatus.includes("10:30"),
        gapStatus.slice(0, 80));

  await evalJs(`(() => {
      document.querySelector('#s_meal').value = 'lunch';
      document.querySelector('#s_protein').value = 35;
      document.querySelector('#s_budget').value = 25;
      document.querySelector('#s-plan').click();
    })()`);
  await waitFor(`typeof S !== 'undefined' && S && S.items`, "时段边界方案");
  const afterPlan = await evalJs(`document.querySelector('#s-status').textContent`);
  check("出方案后仍保留时段提示",
        afterPlan.includes("两个供餐时段之间"), afterPlan.slice(0, 80));

  // ------------------------------------------------------ quote failure
  await send("Page.navigate", { url: FAIL });
  await waitFor(`document.querySelector('#meta') && document.querySelector('#meta').textContent.includes('门店')`,
    "报价失败页加载");
  await evalJs(`(() => {
      document.querySelector('#s_meal').value = 'lunch';
      document.querySelector('#s_protein').value = 40;
      document.querySelector('#s_budget').value = 45;
      document.querySelector('#s-plan').click();
    })()`);
  await waitFor(`typeof S !== 'undefined' && S && S.items && S.items.length > 0`, "报价失败页方案");
  const refCost = await evalJs(`S.cost`);
  await evalJs(`document.querySelector('#s-quote').click()`);
  await waitFor(`S && S.quotes && S.quotes.lunch && S.quotes.lunch.status`, "报价返回");
  const fail = await evalJs(`JSON.stringify({
      status: document.querySelector('#s-status').textContent,
      quote: S.quotes.lunch.status,
      amount: S.quotes.lunch.amount,
      head: document.querySelector('#s-result .single-head').textContent,
      line: document.querySelector('#s-result .sub').textContent
    })`).then(JSON.parse);
  check("报价失败状态为 failed", fail.quote === "failed", JSON.stringify(fail.quote));
  check("页面显示试算失败", fail.status.includes("试算失败"), fail.status.slice(0, 60));
  check("报价失败不显示 0 元",
        !/0\.00\s*元/.test(fail.head) && fail.head.includes("参考价"),
        fail.head.replace(/\s+/g, " ").slice(0, 60));
  check("报价失败保留菜单参考价",
        refCost > 0 && fail.head.includes(`参考价 ${refCost.toFixed(2)} 元`),
        `参考价 ${refCost} / ${fail.head.replace(/\s+/g, " ").slice(0, 50)}`);

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
