// A 阶段验收截图：实时服务（页头必须显示「实时」，鳕鱼堡 25.00）+ 离线服务（橙色徽标）。
// Usage: node tests/shot_data_source.mjs [liveUrl] [offlineUrl]
import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { waitForJson } from "./cdp_ready.mjs";

const LIVE = process.argv[2] || "http://127.0.0.1:8811/";
const OFFLINE = process.argv[3] || "http://127.0.0.1:8810/";
const OUT = new URL("../data/fixtures/datafix/", import.meta.url).pathname;
const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PORT = 9346;

const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"} · ${name}${detail ? " · " + detail : ""}`);
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const CN = new Date(Date.now() + 8 * 3600 * 1000);
CN.setUTCDate(CN.getUTCDate() + 1);
const DATE = CN.toISOString().slice(0, 10);

async function main() {
  mkdirSync(OUT, { recursive: true });
  const proc = spawn(CHROME, [
    "--headless=new", "--disable-gpu", "--no-sandbox", "--no-first-run", "--no-proxy-server",
    `--remote-debugging-port=${PORT}`, "--user-data-dir=/tmp/chrome-cdp-shot",
    "about:blank",
  ], { stdio: "ignore" });
  await sleep(2500);
  await waitForJson(`http://127.0.0.1:${PORT}/json/version`);

  const tab = await (await fetch(`http://127.0.0.1:${PORT}/json/new?${encodeURIComponent(LIVE)}`,
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
    const mid = ++id; pending.set(mid, resolve);
    ws.send(JSON.stringify({ id: mid, method: m, params: p }));
  });
  const evalJs = async (e) => {
    const r = await send("Runtime.evaluate", { expression: e, awaitPromise: true, returnByValue: true });
    return r.result?.result?.value;
  };
  const waitFor = async (e, label, timeout = 90000) => {
    const t0 = Date.now();
    while (Date.now() - t0 < timeout) {
      if (await evalJs(e)) return true;
      await sleep(400);
    }
    check(`等待超时：${label}`, false, e.slice(0, 60));
    return false;
  };
  const shot = async (file) => {
    const r = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
    writeFileSync(OUT + file, Buffer.from(r.result.data, "base64"));
    console.log(`  · 截图 ${file}`);
  };

  await send("Page.enable");
  await send("Runtime.enable");
  await send("Emulation.setDeviceMetricsOverride",
    { width: 1280, height: 1100, deviceScaleFactor: 1, mobile: false });

  // =============================================================== live
  await send("Page.navigate", { url: LIVE });
  await waitFor(`document.querySelector('#data-source') && document.querySelector('#data-source').getAttribute('data-source') === 'live'`, "实时徽标");
  await waitFor(`typeof LAND !== 'undefined' && LAND && LAND.items && LAND.items.length > 0`, "实时图表");

  await evalJs(`(() => {
      document.querySelector('#s_meal').value = 'lunch';
      document.querySelector('#s_protein').value = 44;
      document.querySelector('#s_budget').value = 45;
      document.querySelector('#s-plan').click();
    })()`);
  await waitFor(`typeof S !== 'undefined' && S && S.items && S.items.length > 0`, "实时方案");
  await sleep(600);
  // 滚到排行表，让鳕鱼堡/麦香鸡的价格出现在截图里
  await evalJs(`document.querySelector('#rank-table') && document.querySelector('#rank-table').scrollIntoView({block:'center'})`);
  await sleep(300);
  await shot("live_lunch_44_45.png");

  const badge = await evalJs(`document.querySelector('#data-source').textContent.trim()`);
  check("实时截图徽标含实时数据", badge.includes("实时数据"), badge);
  check("实时徽标含门店名", /麦当劳|餐厅/.test(badge), badge);

  // 鳕鱼堡与麦香鸡的实时价必须是 25.00 / 16.50（不是 fixture 的 24.00 / 17.00）。
  // 从排行榜取，排行榜一定含所有主食，不受当前方案选了谁影响。
  const rank = await evalJs(`JSON.stringify(
      (LAND && LAND.items || []).filter(i => /鳕鱼|麦香鸡/.test(i.name))
        .map(i => ({ name: i.name, price: i.price })))`).then(JSON.parse);
  const cod = rank.find((p) => /鳕鱼/.test(p.name));
  const chick = rank.find((p) => p.name === "麦香鸡");
  check("实时鳕鱼堡价格为 25.00", cod && Number(cod.price) === 25,
    cod ? `${cod.name} ${cod.price}` : "排行榜无鳕鱼堡");
  check("实时麦香鸡价格为 16.50", chick && Number(chick.price) === 16.5,
    chick ? `${chick.name} ${chick.price}` : "排行榜无麦香鸡");

  const meta = await evalJs(`document.querySelector('#meta').textContent.trim()`);
  check("实时截图数据说明非空", meta && meta.length > 20, String(meta).slice(0, 40));

  // ============================================================== offline
  await send("Page.navigate", { url: OFFLINE });
  await waitFor(`document.querySelector('#data-source') && document.querySelector('#data-source').getAttribute('data-source') === 'offline'`, "离线徽标");
  await evalJs(`(() => {
      document.querySelector('#s_meal').value = 'lunch';
      document.querySelector('#s_protein').value = 44;
      document.querySelector('#s_budget').value = 45;
      document.querySelector('#s-plan').click();
    })()`);
  await waitFor(`typeof S !== 'undefined' && S && S.items && S.items.length > 0`, "离线方案");
  await sleep(600);
  await evalJs(`document.querySelector('#rank-table') && document.querySelector('#rank-table').scrollIntoView({block:'center'})`);
  await sleep(300);
  await shot("offline_lunch_44_45.png");

  const offBadge = await evalJs(`document.querySelector('#data-source').textContent.trim()`);
  check("离线截图徽标为橙色文案", offBadge.includes("离线样例数据") && offBadge.includes("价格不是门店实时价"), offBadge);

  const offRank = await evalJs(`JSON.stringify(
      (LAND && LAND.items || []).filter(i => /鳕鱼|麦香鸡/.test(i.name))
        .map(i => ({ name: i.name, price: i.price })))`).then(JSON.parse);
  const offCod = offRank.find((p) => /鳕鱼/.test(p.name));
  const offChick = offRank.find((p) => p.name === "麦香鸡");
  check("离线鳕鱼堡为 fixture 价 24.00（证明徽标必要）", offCod && Number(offCod.price) === 24,
    offCod ? `${offCod.name} ${offCod.price}` : "排行榜无鳕鱼堡");
  check("离线麦香鸡为 fixture 价 17.00（证明徽标必要）", offChick && Number(offChick.price) === 17,
    offChick ? `${offChick.name} ${offChick.price}` : "排行榜无麦香鸡");

  await sleep(300);
  try { ws.close(); } catch {}
  try { proc.kill("SIGKILL"); } catch {}

  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
  if (failed.length) { failed.forEach((f) => console.log(`FAILED: ${f.name} ${f.detail}`)); process.exit(1); }
}

main().catch((e) => { console.error("FATAL", e); process.exit(1); });
