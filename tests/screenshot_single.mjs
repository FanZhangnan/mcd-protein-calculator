// Capture two single-meal screenshots via CDP:
//   1) positive lunch result with 去高达干酪片 ticked (strict notes visible)
//   2) infeasible lunch with reachable ceiling
// Usage: node tests/screenshot_single.mjs [url] [outDir]
import { spawn } from "node:child_process";
import { writeFileSync, mkdirSync } from "node:fs";

const URL_ = process.argv[2] || "http://127.0.0.1:8773/";
const OUT = process.argv[3] || "data/fixtures/single_meal";
const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PORT = 9335;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  mkdirSync(OUT, { recursive: true });
  const proc = spawn(CHROME, [
    "--headless=new", "--disable-gpu", "--no-sandbox", "--no-first-run",
    `--remote-debugging-port=${PORT}`, "--user-data-dir=/tmp/chrome-cdp-shot",
    "--window-size=1280,1800", "about:blank",
  ], { stdio: "ignore" });
  await sleep(2500);
  const tab = await (await fetch(`http://127.0.0.1:${PORT}/json/new?${encodeURIComponent(URL_)}`,
    { method: "PUT" })).json();
  const ws = new WebSocket(tab.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });

  let id = 0;
  const pending = new Map();
  ws.onmessage = (msg) => {
    const d = JSON.parse(msg.data);
    if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); }
  };
  const send = (m, p = {}) => new Promise((resolve) => {
    const mid = ++id;
    pending.set(mid, resolve);
    ws.send(JSON.stringify({ id: mid, method: m, params: p }));
  });
  const evalJs = async (expr) => (await send("Runtime.evaluate", {
    expression: expr, awaitPromise: true, returnByValue: true,
  })).result?.result?.value;
  const waitFor = async (expr, t = 90000) => {
    const t0 = Date.now();
    while (Date.now() - t0 < t) { if (await evalJs(expr)) return true; await sleep(400); }
    return false;
  };
  const shot = async (name) => {
    const r = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
    writeFileSync(`${OUT}/${name}.png`, Buffer.from(r.result.data, "base64"));
    console.log("saved", `${OUT}/${name}.png`);
  };

  await send("Page.enable");
  await send("Runtime.enable");
  await send("Page.navigate", { url: URL_ });
  await waitFor(`document.querySelector('#meta') && document.querySelector('#meta').textContent.includes('门店')`);

  await evalJs(`(() => {
      document.querySelector('#s_meal').value = 'lunch';
      document.querySelector('#s_protein').value = 30;
      document.querySelector('#s_fat').value = 60;
      document.querySelector('#s_budget').value = 40;
      document.querySelector('#s-plan').click();
      return true;
    })()`);
  await waitFor(`typeof S !== 'undefined' && S && S.meals && S.meals.length === 1`);
  await evalJs(`(() => {
      const el = document.querySelector('input.srem[data-code="102215"]');
      el.checked = true;
      el.dispatchEvent(new Event('change', {bubbles: true}));
      return true;
    })()`);
  await waitFor(`S && S.meals[0].items[0].nutrition_basis === 'standard_unadjusted'`);
  await evalJs(`document.querySelector('#s-quote').click()`);
  await waitFor(`S && S.quotes && S.quotes.lunch && S.quotes.lunch.status === 'ok'`);
  await sleep(600);
  await shot("case4_cheese_removal");

  await evalJs(`(() => {
      document.querySelector('#s_protein').value = 40;
      document.querySelector('#s_fat').value = 20;
      document.querySelector('#s_budget').value = 35;
      document.querySelector('#s-plan').click();
      return true;
    })()`);
  await waitFor(`S && S.feasible === false && S.frontier`);
  await sleep(600);
  await shot("case2_infeasible");

  await evalJs(`(() => {
      document.querySelector('#s_meal').value = 'lunch';
      document.querySelector('#s_protein').value = 30;
      document.querySelector('#s_fat').value = 20;
      document.querySelector('#s_budget').value = 30;
      document.querySelector('#s-plan').click();
      return true;
    })()`);
  await waitFor(`S && S.meal === 'lunch' && S.feasible === false && S.frontier`);
  await sleep(600);
  await shot("case1_lunch_30_20_30");

  await evalJs(`(() => {
      document.querySelector('#s_meal').value = 'breakfast';
      document.querySelector('#s_protein').value = 25;
      document.querySelector('#s_fat').value = 15;
      document.querySelector('#s_budget').value = 20;
      document.querySelector('#s-plan').click();
      return true;
    })()`);
  await waitFor(`S && S.meal === 'breakfast' && S.feasible === false && S.frontier`);
  await sleep(600);
  await shot("case3_breakfast_25_15_20");

  try { ws.close(); } catch {}
  try { proc.kill("SIGKILL"); } catch {}
}

main().catch((e) => { console.error(e); process.exit(1); });
