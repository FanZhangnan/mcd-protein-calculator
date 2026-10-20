// Live screenshots + UI assertions for the five acceptance cases.
// Usage: node tests/screenshot_muscle.mjs [url]
import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";

const URL_ = process.argv[2] || "http://127.0.0.1:8775/";
const OUT = new URL("../data/fixtures/muscle/", import.meta.url).pathname;
const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PORT = 9336;

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
    `--remote-debugging-port=${PORT}`, "--user-data-dir=/tmp/chrome-cdp-shots",
    "about:blank",
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
      const ex = r.result.exceptionDetails;
      console.log("JS ERROR", ex.text, ex.exception?.description || ex.exception?.value || "");
    }
    return r.result?.result?.value;
  };
  const waitFor = async (expr, label, timeout = 120000) => {
    const t0 = Date.now();
    while (Date.now() - t0 < timeout) {
      if (await evalJs(expr)) return true;
      await sleep(500);
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
  check("页面数据来源显示实时",
        (await evalJs(`document.querySelector('#meta').textContent.includes('实时')`)) === true);

  const setInputs = async (meal, protein, budget, fat, maxMains) => {
    await evalJs(`(() => {
        document.querySelector('#s_meal').value = '${meal}';
        document.querySelector('#s_protein').value = '${protein}';
        document.querySelector('#s_budget').value = '${budget}';
        document.querySelector('#s_fat').value = '${fat}';
        document.querySelector('#s_max_mains').value = '${maxMains}';
        document.querySelector('#s_side').checked = false;
        document.querySelector('#s_meal').dispatchEvent(new Event('change'));
      })()`);
    await waitFor(`LAND && LAND.meal === '${meal}'`, "性价比图切换");
    await evalJs(`S = null; document.querySelector('#s-plan').click()`);
    await waitFor(`S && S.meal === '${meal}' && typeof S.items !== 'undefined'`, "方案返回");
    await sleep(400);
  };

  const shot = async (name) => {
    const metrics = await send("Page.getLayoutMetrics");
    const height = Math.ceil(metrics.result.cssContentSize.height);
    const image = await send("Page.captureScreenshot", {
      format: "png", captureBeyondViewport: true,
      clip: { x: 0, y: 0, width: 1280, height, scale: 1 },
    });
    writeFileSync(`${OUT}${name}.png`, Buffer.from(image.result.data, "base64"));
    return height;
  };

  // case 1
  await setInputs("lunch", 40, 45, "", 2);
  check("用例1 页面显示达标", (await evalJs(`S.feasible === true`)) === true,
        await evalJs(`S.items.map(i => i.name + '×' + i.quantity).join(' + ')`));
  await shot("case1_lunch_40_45");

  // case 2
  await setInputs("lunch", 40, 25, "", 2);
  const c2 = await evalJs(`JSON.stringify({
      feasible: S.feasible, cost: S.cost, protein: S.totals.protein,
      text: document.querySelector('#s-result').textContent
    })`);
  const c2j = JSON.parse(c2);
  check("用例2 无解且主卡片在预算内",
        c2j.feasible === false && c2j.cost <= 25, `¥${c2j.cost} / P${c2j.protein}`);
  check("用例2 显示预算内最多的说明",
        c2j.text.includes("最多只能做到")
        && c2j.text.includes("降低目标到"), c2j.text.slice(0, 80));
  await shot("case2_lunch_40_25");

  // case 3
  await setInputs("breakfast", 30, 30, "", 2);
  const c3 = await evalJs(`JSON.stringify({
      feasible: S.feasible, cost: S.cost, protein: S.totals.protein,
      text: document.querySelector('#s-result').textContent
    })`).then(JSON.parse);
  check("用例3 早餐结论一致",
        c3.feasible ? (c3.protein >= 30 && c3.cost <= 30)
                    : (c3.cost <= 30 && c3.protein < 30),
        `feasible=${c3.feasible} P${c3.protein} ¥${c3.cost}`);
  await shot("case3_breakfast_30_30");

  // case 4
  await setInputs("lunch", 40, 45, 40, 2);
  await evalJs(`document.querySelector('#s_fat').closest('details').open = true`);
  const c4 = await evalJs(`JSON.stringify({
      feasible: S.feasible, cost: S.cost, protein: S.totals.protein, fat: S.totals.fat
    })`).then(JSON.parse);
  check("用例4 有解且脂肪不超 40",
        c4.feasible === true && c4.fat <= 40, `P${c4.protein} F${c4.fat} ¥${c4.cost}`);
  await shot("case4_lunch_40_45_fat40");

  // case 5 — body data
  await evalJs(`document.querySelector('#s_sex').closest('details').open = true`);
  await evalJs(`(() => {
      document.querySelector('#s_sex').value = 'male';
      document.querySelector('#s_age').value = 22;
      document.querySelector('#s_height').value = 175;
      document.querySelector('#s_weight').value = 70;
      document.querySelector('#s_meals').value = 3;
      document.querySelector('#s_goal').value = 'muscle';
      document.querySelector('#s-calc').click();
    })()`);
  await sleep(500);
  const c5 = await evalJs(`JSON.stringify({
      protein: document.querySelector('#s_protein').value,
      hint: document.querySelector('#s-cap-hint').textContent,
      note: document.querySelector('#s-calc-note').textContent
    })`).then(JSON.parse);
  check("用例5 预填蛋白质 47 g", c5.protein === "47", c5.protein);
  check("用例5 显示预算内上限提示",
        c5.hint.includes("最多能买到") && c5.hint.includes("g 蛋白质"), c5.hint);
  check("用例5 热量仅参考未预填",
        (await evalJs(`document.querySelector('#s_kcal_max').value === ''
                       && document.querySelector('#s_kcal_min').value === ''`)) === true);
  await shot("case5_body_estimate");

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
