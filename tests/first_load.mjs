// First-load acceptance: a result without clicking, self-hosted font, chart geometry.
// Usage: node tests/first_load.mjs [url] [outDir]
import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";

const URL_ = process.argv[2] || "http://127.0.0.1:8941/";
const OUT = process.argv[3] || new URL("../docs/screenshots/", import.meta.url).pathname;
const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PORT = 9343;

const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok });
  console.log(`${ok ? "PASS" : "FAIL"} · ${name}${detail ? " · " + detail : ""}`);
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  mkdirSync(OUT, { recursive: true });
  const proc = spawn(CHROME, [
    "--headless=new", "--disable-gpu", "--no-sandbox", "--no-first-run", "--no-proxy-server",
    `--remote-debugging-port=${PORT}`, "--user-data-dir=/tmp/chrome-cdp-first", "about:blank",
  ], { stdio: "ignore" });
  await sleep(2500);

  const tab = await (await fetch(`http://127.0.0.1:${PORT}/json/new?${encodeURIComponent(URL_)}`,
    { method: "PUT" })).json();
  const ws = new WebSocket(tab.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });

  let id = 0;
  const pending = new Map();
  const requests = [];
  ws.onmessage = (msg) => {
    const data = JSON.parse(msg.data);
    if (data.id && pending.has(data.id)) { pending.get(data.id)(data); pending.delete(data.id); }
    if (data.method === "Network.requestWillBeSent") requests.push(data.params.request.url);
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
  const waitFor = async (expr, label, timeout = 8000) => {
    const t0 = Date.now();
    while (Date.now() - t0 < timeout) {
      if (await evalJs(expr)) return true;
      await sleep(150);
    }
    check(`等待超时：${label}`, false, expr.slice(0, 70));
    return false;
  };
  const shot = async (name) => {
    // 只要首屏：不裁剪、不拼接整页
    const image = await send("Page.captureScreenshot", { format: "png" });
    writeFileSync(`${OUT}${name}.png`, Buffer.from(image.result.data, "base64"));
  };

  await send("Page.enable");
  await send("Runtime.enable");
  await send("Network.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: 1280, height: 1000, deviceScaleFactor: 1, mobile: false });
  const t0 = Date.now();
  await send("Page.navigate", { url: URL_ });
  const appeared = await waitFor(
    `!document.querySelector('#s-result-panel').hidden && document.querySelector('#s-result').textContent.includes('42')`,
    "首屏自动结果", 8000);
  check("打开 3 秒内出现结果卡", appeared && Date.now() - t0 < 3000, `${Date.now() - t0}ms`);

  const info = await evalJs(`JSON.stringify({
    status: S && S.protein_status, protein: S && S.totals && S.totals.protein,
    cost: S && S.cost, items: S && S.items ? S.items.map(i => i.name + '×' + i.quantity) : [],
    rec: !!document.querySelector('#chart .rec-marker'),
    text: document.querySelector('#s-result').textContent
  })`).then(JSON.parse);
  // 菜单里麦香鸡 13.5 → 16.5 元、单轮商品「牛肉堡单品精选」以 14 元的汉堡包
  // 进入主食池之后，默认 40 g / 45 元的最省解变成 双层吉士 + 汉堡包 = 40 g / 36.50 元
  check("默认值出 40 g / 36.50 元", info.protein === 40 && info.cost === 36.5,
    `${info.protein} g / ${info.cost} 元`);
  check("图上有推荐星标", info.rec === true);

  const quoteReqs = requests.filter((u) => u.includes("/api/quote"));
  check("自动计算未请求报价或券后价", quoteReqs.length === 0, quoteReqs.join(", "));

  const font = await evalJs(`(async () => {
    await document.fonts.ready;
    const faces = [...document.fonts].filter(f => f.family.includes('Barlow'));
    const loaded = faces.filter(f => f.status === 'loaded').map(f => f.weight);
    return JSON.stringify({loaded, check: document.fonts.check('700 16px "Barlow Condensed"'),
      google: [...document.querySelectorAll('link[href*="fonts.g"]')].length});
  })()`).then(JSON.parse);
  check("Barlow Condensed 600/700 已加载",
    font.loaded.includes("600") && font.loaded.includes("700") && font.check === true, JSON.stringify(font));
  const googleReqs = requests.filter((u) => u.includes("fonts.googleapis.com") || u.includes("fonts.gstatic.com"));
  check("无 Google Fonts 请求", googleReqs.length === 0, googleReqs.join(", "));

  const tick = await evalJs(`(() => {
    const t = document.querySelector('#chart text.tick');
    return t ? +t.getBoundingClientRect().height.toFixed(2) : 0;
  })()`);
  check("1280px 坐标文字显示高度 11–16px", tick >= 11 && tick <= 16, `${tick}px`);

  const geo = await evalJs(`(() => {
    const r = document.querySelector('#chart rect.region');
    const b = document.querySelector('#chart rect.band');
    const nums = [...document.querySelectorAll('#chart text.dot-num')].map(e => {
      const m = /translate\\(([-\\d.]+)[ ,]([-\\d.]+)\\)/.exec(e.getAttribute('transform') || '');
      return { cx: +(e.dataset.cx), cy: +(e.dataset.cy) };
    });
    let min = Infinity;
    for (let i = 0; i < nums.length; i++) for (let j = i + 1; j < nums.length; j++) {
      min = Math.min(min, Math.hypot(nums[i].cx - nums[j].cx, nums[i].cy - nums[j].cy));
    }
    const card = document.querySelector('#input-panel');
    const kids = [...card.children].filter(el => getComputedStyle(el).display !== 'none');
    const lastBottom = Math.max(...kids.map(el => el.getBoundingClientRect().bottom));
    const bottomGap = card.getBoundingClientRect().bottom - lastBottom;
    return JSON.stringify({
      regionW: r ? +r.getAttribute('width') : null,
      bandW: b ? +b.getAttribute('width') : null,
      minDot: Number.isFinite(min) ? +min.toFixed(1) : null,
      bottomGap: +bottomGap.toFixed(1),
    });
  })()`).then(JSON.parse);
  check("容差带右边界 = 达标区右边界",
    geo.regionW !== null && Math.abs(geo.regionW - geo.bandW) < 0.6,
    `region=${geo.regionW} band=${geo.bandW}`);
  check("编号圆点间距 ≥ 18px", geo.minDot === null || geo.minDot >= 18, `${geo.minDot}`);
  check("输入卡底部空白 ≤ 24px", geo.bottomGap <= 24, `${geo.bottomGap}px`);

  await shot("demo-first-load-1280");

  await send("Emulation.setDeviceMetricsOverride", { width: 800, height: 1000, deviceScaleFactor: 1, mobile: false });
  await sleep(400);
  const tick800 = await evalJs(`(() => {
    const t = document.querySelector('#chart text.tick');
    return t ? +t.getBoundingClientRect().height.toFixed(2) : 0;
  })()`);
  check("800px 坐标文字显示高度 11–16px", tick800 >= 11 && tick800 <= 16, `${tick800}px`);

  // 手机首屏：重新加载，不做任何操作
  await send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
  await send("Page.navigate", { url: URL_ });
  await waitFor(`!document.querySelector('#s-result-panel').hidden`, "手机首屏结果", 8000);
  await sleep(500);
  const mobile = await evalJs(`JSON.stringify({
    scroll: document.documentElement.scrollWidth, inner: window.innerWidth,
    pillHidden: getComputedStyle(document.querySelector('.site-head .brand-pill')).display === 'none',
    footPill: getComputedStyle(document.querySelector('.foot-pill')).display !== 'none',
    gh: !!document.querySelector('.gh-btn')
  })`).then(JSON.parse);
  check("手机无横向滚动", mobile.scroll <= mobile.inner + 1, `${mobile.scroll}/${mobile.inner}`);
  check("手机页头胶囊隐藏、页脚胶囊显示", mobile.pillHidden && mobile.footPill, JSON.stringify(mobile));
  check("手机页头有 GitHub 按钮", mobile.gh === true);
  await shot("demo-first-load-390");

  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} passed`);
  ws.close(); proc.kill();
  if (failed.length) process.exitCode = 1;
}
main().catch((e) => { console.error(e); process.exit(2); });
