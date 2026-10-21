// Post-coupon price in the real page (live server required).
// Usage: node tests/coupon_ui.mjs http://127.0.0.1:8945/
import { spawn } from "node:child_process";

const URL_ = process.argv[2] || "http://127.0.0.1:8945/";
const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PORT = 9342;
const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok });
  console.log(`${ok ? "PASS" : "FAIL"} · ${name}${detail ? " · " + detail : ""}`);
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const proc = spawn(CHROME, [
    "--headless=new", "--disable-gpu", "--no-sandbox", "--no-first-run", "--no-proxy-server",
    `--remote-debugging-port=${PORT}`, "--user-data-dir=/tmp/chrome-cdp-coupon", "about:blank",
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
    const mid = ++id; pending.set(mid, resolve);
    ws.send(JSON.stringify({ id: mid, method, params }));
  });
  const evalJs = async (expr) => {
    const r = await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true });
    if (r.result?.exceptionDetails) console.log("JS ERROR", r.result.exceptionDetails.text);
    return r.result?.result?.value;
  };
  const waitFor = async (expr, label, timeout = 60000) => {
    const t0 = Date.now();
    while (Date.now() - t0 < timeout) {
      if (await evalJs(expr)) return true;
      await sleep(400);
    }
    check(`等待超时：${label}`, false, expr.slice(0, 60));
    return false;
  };

  await send("Page.enable");
  await send("Runtime.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: 1280, height: 1000, deviceScaleFactor: 1, mobile: false });
  await send("Page.navigate", { url: URL_ });
  await waitFor(`document.querySelector('#meta') && document.querySelector('#meta').textContent.includes('门店')`, "页面加载");

  await evalJs(`(() => {
    document.querySelector('#s_meal').value = 'lunch';
    document.querySelector('#s_protein').value = '24';
    document.querySelector('#s_budget').value = '23.2';
    document.querySelector('#s_max_mains').value = '1';
    document.querySelector('#s_kcal_min').value = '450';
    document.querySelector('#s_fat').value = '';
    document.querySelector('#s_side').checked = false;
    document.querySelector('#s_meal').dispatchEvent(new Event('change'));
  })()`);
  await sleep(600);
  await evalJs(`document.querySelector('#s-plan').click()`);
  await waitFor(`S && S.meal === 'lunch' && S.items && S.items.length === 1`, "方案返回");
  await waitFor(`document.querySelector('.coupon-line') && !document.querySelector('#coupon-pending')`, "券后价返回", 90000);
  await sleep(600);

  const info = await evalJs(`JSON.stringify({
    text: document.querySelector('.coupon-line') ? document.querySelector('.coupon-line').textContent : '',
    ok: !!document.querySelector('.coupon-line.ok'),
    price: document.querySelector('.single-card .big') ? document.querySelector('.single-card .big').textContent : '',
    tag: document.querySelector('.price-tag') ? document.querySelector('.price-tag').textContent : '',
    body: document.body.innerHTML.includes('couponId') || document.body.innerHTML.includes('couponCode')
  })`).then(JSON.parse);

  check("显示券后价 11.90 元", info.text.includes("券后 11.90 元"), info.text.slice(0, 120));
  check("写清用了哪张券", info.text.includes("11.9元麦辣鸡腿堡"));
  check("原价 23.00 保留并划线", info.text.includes("原价") && info.text.includes("23.00"));
  check("显示券后每元蛋白质", info.text.includes("券后每元 2.0 g"));
  check("标注下单以 App 为准", info.text.includes("下单时以 App 为准"));
  check("价格区显示券后价", info.tag.includes("券后价"), info.tag);
  check("页面不出现 couponId/couponCode", info.body === false);

  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} passed`);
  ws.close(); proc.kill();
  if (failed.length) process.exitCode = 1;
}
main().catch((e) => { console.error(e); process.exit(2); });
