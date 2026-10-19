// Single-meal UI, real Chrome via CDP.
// Usage: node tests/browser_single.mjs [url]
//
// Positive: lunch 20/20/30 -> one main -> tick 点单建议 -> quote
// Negative: lunch 99/20/35 -> no card beyond caps, ceiling text
import { spawn } from "node:child_process";

const URL_ = process.argv[2] || "http://127.0.0.1:8773/";
const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PORT = 9334;

const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"} · ${name}${detail ? " · " + detail : ""}`);
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const proc = spawn(CHROME, [
    "--headless=new", "--disable-gpu", "--no-sandbox", "--no-first-run",
    `--remote-debugging-port=${PORT}`, "--user-data-dir=/tmp/chrome-cdp-single",
    "about:blank",
  ], { stdio: "ignore" });
  await sleep(2500);

  const version = await (await fetch(`http://127.0.0.1:${PORT}/json/version`)).json();
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
  const waitFor = async (expr, label, timeout = 60000) => {
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
  check("页面没有全天模式切换",
        (await evalJs(`!document.querySelector('#mode-day') && !document.querySelector('#day-mode')`)) === true);

  // ---------------------------------------------------------- positive flow
  await evalJs(`(() => {
      document.querySelector('#s_meal').value = 'lunch';
      document.querySelector('#s_protein').value = 20;
      document.querySelector('#s_fat').value = 20;
      document.querySelector('#s_budget').value = 30;
      document.querySelector('#s_side').checked = false;
      return true;
    })()`);
  await evalJs(`document.querySelector('#s-plan').click()`);
  await waitFor(`typeof S !== 'undefined' && S && S.items && S.items.length >= 1`, "单餐推荐", 90000);

  const base = await evalJs(`JSON.stringify({
      feasible: S.feasible, code: S.items[0].code, name: S.items[0].name,
      qty: S.items[0].quantity,
      mains: S.items.filter(i => i.kind === 'main').length,
      kcal: S.totals.energyKcal, protein: S.totals.protein,
      cost: S.cost, sig: S.signature,
      sugg: (S.suggestions || []).map(o => o.code)
    })`).then(JSON.parse);
  check("结构锁定：恰好 1 个主食、数量 1",
        base.mains === 1 && base.qty === 1, JSON.stringify(base).slice(0, 160));
  check("结果卡显示严格模式与官方",
        (await evalJs(`document.querySelector('#s-result').textContent.includes('严格模式')
                       && document.querySelector('#s-result').textContent.includes('官方')`)) === true);
  check("出现可勾选的点单建议", base.sugg.length > 0, JSON.stringify(base.sugg));

  const beforeKcal = base.kcal;
  await evalJs(`(() => {
      const el = document.querySelector('input.srem');
      if (!el) return false;
      el.checked = true;
      el.dispatchEvent(new Event('change', {bubbles: true}));
      return true;
    })()`);
  await waitFor(`S && S.items[0].nutrition_basis === 'standard_unadjusted'`, "点单建议重算", 60000);

  const removed = await evalJs(`JSON.stringify({
      kcal: S.totals.energyKcal, protein: S.totals.protein,
      basis: S.items[0].nutrition_basis, est: S.items[0].is_estimate,
      mod: S.items[0].modification,
      notes: S.items[0].removal_notes,
      strictNotes: document.querySelectorAll('.strict-note').length
    })`).then(JSON.parse);
  check("去料后营养完全不变",
        removed.kcal === beforeKcal && removed.protein === base.protein,
        `${removed.kcal}/${removed.protein} vs ${beforeKcal}/${base.protein}`);
  check("nutrition_basis=standard_unadjusted 且非估算",
        removed.basis === "standard_unadjusted" && removed.est === false);
  check("显示严格模式文案", removed.strictNotes >= 1
        && removed.notes.some(n => n.includes("标准版数值")),
        JSON.stringify(removed.notes).slice(0, 160));
  check("点单参数覆盖整组", removed.mod.length >= 1,
        JSON.stringify(removed.mod).slice(0, 160));

  await evalJs(`document.querySelector('#s-quote').click()`);
  await waitFor(`S && S.quotes && S.quotes.lunch && S.quotes.lunch.status === 'ok'`, "单餐报价", 90000);
  const quoted = await evalJs(`JSON.stringify({
      amount: S.quotes.lunch.amount, cost: S.cost,
      kcal: S.totals.energyKcal, protein: S.totals.protein,
      requested: S.quotes.lunch.requested_items,
      qty: S.items[0].quantity,
      statusText: document.querySelector('#s-status').textContent
    })`).then(JSON.parse);
  check("报价价差为 0", Math.abs(quoted.amount - quoted.cost) < 0.01,
        `${quoted.amount} vs ${quoted.cost}`);
  check("报价请求带 modification", quoted.requested[0].modification.values.length >= 1,
        JSON.stringify(quoted.requested[0].modification).slice(0, 140));
  check("报价后数量与营养不变",
        quoted.qty === 1 && quoted.kcal === beforeKcal && quoted.protein === base.protein);
  check("页面显示试算价而非实付",
        quoted.statusText.includes("试算成功") && !quoted.statusText.includes("实付"),
        quoted.statusText.slice(0, 80));

  // ---------------------------------------------------------- negative flow
  await evalJs(`(() => {
      document.querySelector('#s_protein').value = 99;
      document.querySelector('#s_fat').value = 20;
      document.querySelector('#s_budget').value = 35;
      document.querySelector('#s-plan').click();
      return true;
    })()`);
  await waitFor(`S && S.feasible === false && S.frontier`, "无解方案", 90000);
  const negText = await evalJs(`document.querySelector('#s-result').textContent`);
  check("无解时说明达不到蛋白质目标", negText.includes("达不到 99 g 蛋白质"),
        negText.slice(0, 100));
  check("无解时给出可达上限", negText.includes("蛋白质最多能达到"), negText.slice(-220));
  check("无解时给出放宽方案", negText.includes("只放宽价格"));
  check("无解卡不冒充达标", !negText.includes("满足硬约束")
        && (await evalJs(`S.frontier.max_protein.fat <= 20 && S.frontier.max_protein.cost <= 35`)) === true);

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
