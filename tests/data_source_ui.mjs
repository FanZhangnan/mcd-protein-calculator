// A 阶段数据源显示验证：徽标三态、数据说明不为空、刻度为整数。
// 必须配两个服务：
//   离线：MAI_MAI_DATA=/tmp/x python3 src/server.py --offline --port 8810
//   实时：MAI_MAI_DATA=/tmp/y python3 src/server.py --port 8811
// Usage: node tests/data_source_ui.mjs [offlineUrl] [liveUrl]
import { spawn } from "node:child_process";
import { waitForJson } from "./cdp_ready.mjs";

const OFFLINE = process.argv[2] || "http://127.0.0.1:8810/";
const LIVE = process.argv[3] || "http://127.0.0.1:8811/";
const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PORT = 9345;

const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"} · ${name}${detail ? " · " + detail : ""}`);
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const proc = spawn(CHROME, [
    "--headless=new", "--disable-gpu", "--no-sandbox", "--no-first-run", "--no-proxy-server",
    `--remote-debugging-port=${PORT}`, "--user-data-dir=/tmp/chrome-cdp-dsrc",
    "about:blank",
  ], { stdio: "ignore" });
  await sleep(2500);

  const version = await waitForJson(`http://127.0.0.1:${PORT}/json/version`);
  check("Chrome CDP 可用", !!version.webSocketDebuggerUrl, version.Browser);

  const tab = await (await fetch(`http://127.0.0.1:${PORT}/json/new?${encodeURIComponent(OFFLINE)}`,
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
      console.log("JS ERROR", r.result.exceptionDetails.exception?.description || "");
    }
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

  await send("Page.enable");
  await send("Runtime.enable");

  // ---------------------------------------------------------------- offline
  await send("Page.navigate", { url: OFFLINE });
  await waitFor(`document.querySelector('#data-source') && document.querySelector('#data-source').getAttribute('data-source') && document.querySelector('#data-source').getAttribute('data-source') !== 'none'`, "离线页徽标渲染");

  const offBadge = await evalJs(`JSON.stringify({
      attr: document.querySelector('#data-source').getAttribute('data-source'),
      text: document.querySelector('#data-source').textContent.trim(),
      hasDot: !!document.querySelector('#data-source .src-dot')
    })`).then(JSON.parse);
  check("离线徽标 data-source=offline", offBadge.attr === "offline", offBadge.attr);
  check("离线徽标是橙色文案", offBadge.text.includes("离线样例数据") && offBadge.text.includes("价格不是门店实时价"),
    offBadge.text);
  check("离线徽标带状态点", offBadge.hasDot, String(offBadge.hasDot));

  // 离线：报价按钮带标注
  await evalJs(`(() => {
      document.querySelector('#s_meal').value = 'lunch';
      document.querySelector('#s_protein').value = 40;
      document.querySelector('#s_budget').value = 45;
      document.querySelector('#s-plan').click();
    })()`);
  await waitFor(`typeof S !== 'undefined' && S && S.items`, "离线方案");
  const quoteBtn = await evalJs(`document.querySelector('#s-quote').textContent`);
  check("离线报价按钮标注离线桩", quoteBtn.includes("离线桩报价"), quoteBtn);

  // 数据说明不为空
  const meta = await evalJs(`document.querySelector('#meta').textContent.trim()`);
  check("数据说明非空", meta && meta.length > 20, String(meta).slice(0, 60));
  check("数据说明含门店", meta.includes("门店"), "");
  check("数据说明含日期", meta.includes("日期"), "");
  check("数据说明含餐次时间", meta.includes("餐次时间"), "");
  check("数据说明含数据来源", meta.includes("数据来源"), "");
  check("数据说明含营养条目", meta.includes("营养条目"), "");
  check("数据说明含严格模式", meta.includes("严格模式"), "");

  const unknown = await evalJs(`document.querySelector('#unknown').textContent.trim()`);
  check("未纳入计算商品数显示", /未纳入计算的商品/.test(unknown), String(unknown).slice(0, 50));

  // 刻度为整数（价格轴与蛋白质轴分开检查，两条轴的刻度会交错出现在 SVG 里）
  // x 轴刻度 anchor="middle"，y 轴刻度 anchor="end"
  const tickTexts = await evalJs(`JSON.stringify(
      [...document.querySelectorAll('#chart text.tick')].map(t => t.textContent))`).then(JSON.parse);
  check("刻度全部为整数", tickTexts.every((t) => /^\d+$/.test(t)), tickTexts.join(","));

  const xTicks = await evalJs(`JSON.stringify(
      [...document.querySelectorAll('#chart text.tick')]
        .filter(t => t.getAttribute('text-anchor') === 'middle')
        .map(t => t.textContent))`).then(JSON.parse);
  const yTicks = await evalJs(`JSON.stringify(
      [...document.querySelectorAll('#chart text.tick')]
        .filter(t => t.getAttribute('text-anchor') === 'end')
        .map(t => t.textContent))`).then(JSON.parse);
  const mono = (arr) => arr.every((t, i) => i === 0 || Number(t) > Number(arr[i - 1]));
  check("价格轴刻度单调递增", mono(xTicks), xTicks.join(","));
  check("蛋白质轴刻度单调递增", mono(yTicks), yTicks.join(","));

  // -------------------------------------------------------------------- live
  await send("Page.navigate", { url: LIVE });
  await waitFor(`document.querySelector('#data-source') && ['live','cache'].includes(document.querySelector('#data-source').getAttribute('data-source'))`, "实时页徽标渲染");
  const liveBadge = await evalJs(`JSON.stringify({
      attr: document.querySelector('#data-source').getAttribute('data-source'),
      text: document.querySelector('#data-source').textContent.trim()
    })`).then(JSON.parse);
  check("实时页 data-source=live", liveBadge.attr === "live", liveBadge.attr);
  check("实时徽标文案含实时数据", liveBadge.text.includes("实时数据"), liveBadge.text);
  check("实时徽标含门店名", /麦当劳|餐厅|珠海/.test(liveBadge.text), liveBadge.text);

  await sleep(300);
  try { ws.close(); } catch {}
  try { proc.kill("SIGKILL"); } catch {}

  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
  if (failed.length) {
    failed.forEach((f) => console.log(`FAILED: ${f.name} ${f.detail}`));
    process.exit(1);
  }
}

main().catch((e) => { console.error("FATAL", e); process.exit(1); });
