// 最终 UI 验收：推荐标记 / 达标区 / 标签避让 / 升级超限 / 省钱切换 /
// 桌面 1280 与手机 390 五个场景截图。
// Usage: node tests/ui_final.mjs [url]
import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { waitForJson } from "./cdp_ready.mjs";

const URL_ = process.argv[2] || "http://127.0.0.1:8792/";
const OUT = new URL("../data/fixtures/ui/", import.meta.url).pathname;
const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PORT = 9341;

// 规划日由服务端决定（服务器日期 + 1 天）。这里**不要**再写死日期：
// 写死会把服务切到另一个日期，而套餐目录是按某一个日期校验的，
// 目录一旦对不上就会被整批拒用，页面上的推荐也会跟着变。

const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"} · ${name}${detail ? " · " + detail : ""}`);
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const SCENARIOS = [
  { id: "1_lunch_40_45", meal: "lunch", protein: 40, budget: 45, fat: "", expect: "met" },
  { id: "2_lunch_44_45", meal: "lunch", protein: 44, budget: 45, fat: "", expect: "met", saver: true },
  { id: "3_lunch_35_25", meal: "lunch", protein: 35, budget: 25, fat: "", expect: "under" },
  { id: "4_breakfast_25_20", meal: "breakfast", protein: 25, budget: 20, fat: "", expect: "met" },
  // 「升级超限」样式需要方案里有一款带肉量阶梯的主食。麦香鱼 22 元不再是最优解，
  // 午餐 40 g / 45 元 场景已经没有升级项，改用早餐场景继续覆盖同一条断言。
  { id: "5_breakfast_23_20_fat22", meal: "breakfast", protein: 23, budget: 20, fat: 22, expect: "met", upgradeOver: true },
];

async function main() {
  mkdirSync(OUT, { recursive: true });
  const post = (path, body) => fetch(new URL(path, URL_), {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then((r) => r.json());
  // 只刷新，不改日期：刷新会保留服务端当前的规划日
  const refreshed = await post("/api/refresh", {});
  check("菜单来源实时", refreshed.source === "live", String(refreshed.source));

  const proc = spawn(CHROME, [
    "--headless=new", "--disable-gpu", "--no-sandbox", "--no-first-run", "--no-proxy-server",
    `--remote-debugging-port=${PORT}`, "--user-data-dir=/tmp/chrome-cdp-ui",
    "about:blank",
  ], { stdio: "ignore" });
  const tab = await waitForJson(`http://127.0.0.1:${PORT}/json/new?${encodeURIComponent(URL_)}`,
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

  const setViewport = async (width, height, mobile) => {
    await send("Emulation.setDeviceMetricsOverride", {
      width, height, deviceScaleFactor: mobile ? 2 : 1, mobile,
    });
    await send("Page.navigate", { url: URL_ });
    await waitFor(`document.querySelector('#meta') && document.querySelector('#meta').textContent.includes('门店')`,
      "页面加载");
    await waitFor(`typeof LAND !== 'undefined' && LAND && LAND.items.length > 0`, "性价比图");
  };

  const runScenario = async (sc) => {
    await evalJs(`(() => {
        document.querySelector('#s_meal').value = '${sc.meal}';
        document.querySelector('#s_protein').value = '${sc.protein}';
        document.querySelector('#s_budget').value = '${sc.budget}';
        document.querySelector('#s_fat').value = '${sc.fat}';
        document.querySelector('#s_max_mains').value = '2';
        document.querySelector('#s_tolerance').value = '3';
        document.querySelector('#s_meal').dispatchEvent(new Event('change'));
        document.querySelector('#s_protein').dispatchEvent(new Event('input'));
        document.querySelector('#s_budget').dispatchEvent(new Event('input'));
        document.querySelector('#s_fat').dispatchEvent(new Event('input'));
      })()`);
    await waitFor(`LAND && LAND.meal === '${sc.meal}'`, "性价比图切换");
    // 页面打开时会自动算一次默认方案；必须等到「本次请求」的方案回来
    await evalJs(`window.__sig = (typeof S !== 'undefined' && S && S.signature) || ''`);
    await evalJs(`S = null; document.querySelector('#s-plan').click()`);
    await waitFor(`S && S.meal === '${sc.meal}' && S.protein_status
      && Math.abs(Number(S.targets.protein_min) - ${sc.protein}) < 0.01
      && Math.abs(Number(S.targets.budget) - ${sc.budget}) < 0.01
      && S.signature !== window.__sig`, "方案返回");
    await sleep(250);
  };

  const shot = async (name, width, mobile) => {
    const metrics = await send("Page.getLayoutMetrics");
    const height = Math.ceil(metrics.result.cssContentSize.height);
    const image = await send("Page.captureScreenshot", {
      format: "png", captureBeyondViewport: true,
      clip: { x: 0, y: 0, width, height, scale: mobile ? 2 : 1 },
    });
    writeFileSync(`${OUT}${mobile ? "mobile" : "desktop"}_${name}.png`,
      Buffer.from(image.result.data, "base64"));
  };

  // ------------------------------------------------------------ desktop
  await setViewport(1280, 1000, false);
  for (const sc of SCENARIOS) {
    await runScenario(sc);
    const info = await evalJs(`JSON.stringify({
        status: S.protein_status, cost: S.cost, protein: S.totals.protein,
        items: S.items.map(i => i.name + '×' + i.quantity),
        saver: S.saver ? {cost: S.saver.cost, protein: S.saver.protein} : null,
        rec: (() => { const el = document.querySelector('.rec-marker');
          return el ? {x: +el.dataset.x, y: +el.dataset.y} : null; })(),
        region: (() => { const r = document.querySelector('#chart rect.region');
          return r ? {w: +r.dataset.w, h: +r.dataset.h} : null; })(),
        nums: [...document.querySelectorAll('#chart text.dot-num')]
          .filter(e => getComputedStyle(e).display !== 'none' && e.getAttribute('opacity') !== '0')
          .map(e => ({n: e.textContent,
                      cx: +(e.dataset.cx || e.getAttribute('x')),
                      cy: +(e.dataset.cy || e.getAttribute('y')),
                      l: e.getBBox().x, r: e.getBBox().x + e.getBBox().width,
                      t: e.getBBox().y, b: e.getBBox().y + e.getBBox().height})),
        hots: [...document.querySelectorAll('#chart circle.hot')].map(e => ({
          cx: +e.getAttribute('cx'), cy: +e.getAttribute('cy'), r: +e.getAttribute('r') })),
        dots: [...document.querySelectorAll('#chart circle.dot')].map(e => {
          const b = e.getBBox(); return {l:b.x, r:b.x+b.width, t:b.y, b:b.y+b.height}; })
      })`).then(JSON.parse);
    check(`场景${sc.id} 状态为 ${sc.expect}`, info.status === sc.expect, info.status);
    check(`场景${sc.id} 推荐标记存在且坐标正确`, !!info.rec, JSON.stringify(info.rec));
    // 轴上限与推荐星标位置都按页面实际使用的 niceTicks 口径计算
    const expected = await evalJs(`(() => {
        const items = LAND.items;
        const target = Number(document.querySelector('#s_protein').value);
        const budget = Number(document.querySelector('#s_budget').value);
        const rec = (S && S.items && S.items.length && Number(S.cost) > 0)
          ? {price: Number(S.cost), protein: Number(S.totals.protein)} : null;
        const saver = (S && S.saver) ? {price: Number(S.saver.cost), protein: Number(S.saver.protein)} : null;
        const xs = items.map(d => d.price).concat([Number.isFinite(budget) && budget > 0 ? budget : 0]);
        const ys = items.map(d => d.protein).concat([Number.isFinite(target) && target > 0 ? target : 0]);
        [rec, saver].forEach(p => { if (p) { xs.push(p.price); ys.push(p.protein); } });
        const xMax = niceTicks(Math.max(...xs), [5, 10]).max;
        const yMax = niceTicks(Math.max(...ys), [5, 10, 15]).max;
        const H = document.querySelector('#chart').viewBox.baseVal.height;
        const X = PAD.l + (Math.min(S.cost, xMax) / xMax) * (CHART_W - PAD.l - PAD.r);
        const Y = H - PAD.b - (Math.min(S.totals.protein, yMax) / yMax) * (H - PAD.t - PAD.b);
        return JSON.stringify({x: +X.toFixed(1), y: +Y.toFixed(1), xMax, yMax});
      })()`).then(JSON.parse);
    if (info.rec) {
      check(`场景${sc.id} 标记位置=组合总价/总蛋白`,
        Math.abs(info.rec.x - expected.x) < 1.5 && Math.abs(info.rec.y - expected.y) < 1.5,
        `${JSON.stringify(info.rec)} vs ${JSON.stringify(expected)}`);
    }
    check(`场景${sc.id} 达标区高度>0`, !!info.region && info.region.h > 0,
      JSON.stringify(info.region));
    const axis = await evalJs(`(() => {
        const ticks = [...document.querySelectorAll('#chart text.tick')];
        const yTicks = ticks.filter(t => t.getAttribute('text-anchor') === 'end').map(t => t.textContent);
        const xTicks = ticks.filter(t => t.getAttribute('text-anchor') === 'middle').map(t => t.textContent);
        const mono = a => a.every((t, i) => i === 0 || Number(t) > Number(a[i - 1]));
        return JSON.stringify({ lastY: yTicks[yTicks.length - 1], lastX: xTicks[xTicks.length - 1],
                                allInt: ticks.every(t => /^\\d+$/.test(t.textContent)),
                                monoY: mono(yTicks), monoX: mono(xTicks) });
      })()`).then(JSON.parse);
    check(`场景${sc.id} 纵横轴上限=整数刻度上限`,
      Number(axis.lastY) === expected.yMax && Number(axis.lastX) === expected.xMax,
      `${JSON.stringify(axis)} vs ${JSON.stringify(expected)}`);
    check(`场景${sc.id} 轴刻度为整数且单调`,
      axis.allInt && axis.monoY && axis.monoX, JSON.stringify(axis));
    check(`场景${sc.id} 前 3 编号点全部可见`,
          info.nums.length === Math.min(3, info.dots.length),
          `${info.nums.length}/${Math.min(3, info.dots.length)}`);
    check(`场景${sc.id} 编号点与黑点一一对应`,
          info.nums.length === info.hots.length
          && info.nums.every((n, i) => n.n === String(i + 1)
            && Math.abs(n.cx - info.hots[i].cx) < 0.6 && Math.abs(n.cy - info.hots[i].cy) < 0.6),
          JSON.stringify({nums: info.nums.length, hots: info.hots.length}));
    const overlaps = [];
    for (let i = 0; i < info.nums.length; i += 1) {
      for (let j = i + 1; j < info.nums.length; j += 1) {
        const a = info.nums[i]; const b = info.nums[j];
        if (!(a.r < b.l || a.l > b.r || a.b < b.t || a.t > b.b)) overlaps.push([i, j]);
      }
    }
    check(`场景${sc.id} 编号点互不重叠`, overlaps.length === 0,
      `${info.nums.length} nums, overlaps ${JSON.stringify(overlaps)}`);
    if (sc.saver) {
      check(`场景${sc.id} 有省钱选项`, !!info.saver, JSON.stringify(info.saver));
      const oldSig = await evalJs(`SSIG`);
      await evalJs(`document.querySelector('#s-saver').click()`);
      await waitFor(`S && S.selected === 'saver'`, "切换省钱方案");
      const after = await evalJs(`JSON.stringify({status:S.protein_status, quotes:S.quotes||{}, sig:SSIG})`)
        .then(JSON.parse);
      check(`场景${sc.id} 切换后清空报价`,
        after.status === "near" && !after.quotes[sc.meal] && after.sig !== oldSig,
        JSON.stringify({status: after.status, quotes: after.quotes}));
      await evalJs(`S = null; document.querySelector('#s-plan').click()`);
      await waitFor(`S && S.protein_status === 'met'`, "回到主方案");
    }
    if (sc.upgradeOver) {
      const up = await evalJs(`JSON.stringify({
          has: !!document.querySelector('details.upgrade-box'),
          over: !!document.querySelector('.upgrade.over'),
          text: document.querySelector('details.upgrade-box')?.textContent || ''
        })`).then(JSON.parse);
      check(`场景${sc.id} 升级超限标灰并说明`,
        up.has && up.over && /超出(预算|上限)/.test(up.text),
        up.text.replace(/\s+/g, " ").slice(0, 80));
    }
    await evalJs(`(() => { const d = document.querySelector('#chart circle.dot');
        d.dispatchEvent(new MouseEvent('mouseenter', {bubbles:true, clientX:300, clientY:300})); })()`);
    await sleep(120);
    const tip = await evalJs(`JSON.stringify({hidden: document.querySelector('#chart-tip').hidden,
        text: document.querySelector('#chart-tip').textContent})`).then(JSON.parse);
    check(`场景${sc.id} 悬停提示含价格/蛋白/脂肪/热量/每元`,
      tip.hidden === false && ["价格", "蛋白质", "脂肪", "热量", "每元蛋白质"].every(k => tip.text.includes(k)),
      tip.text.slice(0, 60));
    await evalJs(`document.querySelector('#chart-tip').hidden = true`);
    await shot(sc.id, 1280, false);
    if (sc.id.startsWith("1_")) {
      await evalJs(`document.querySelector('#s-quote').click()`);
      await waitFor(`S && S.quotes && S.quotes[${JSON.stringify(sc.meal)}]`, "报价返回");
      const qt = await evalJs(`JSON.stringify({
          tag: document.querySelector('.price-tag').textContent,
          num: document.querySelector('.metric.price .metric-num').textContent,
          amount: S.quotes[${JSON.stringify(sc.meal)}].amount
        })`).then(JSON.parse);
      check(`场景${sc.id} 报价后只显示试算价并标已试算`,
        qt.tag === "已试算" && Number(qt.num) === Number(qt.amount),
        JSON.stringify(qt));
    }
  }

  // ------------------------------------------------------------- mobile
  await setViewport(390, 844, true);
  for (const sc of SCENARIOS) {
    await runScenario(sc);
    const info = await evalJs(`JSON.stringify({
        status: S.protein_status,
        scrollW: document.documentElement.scrollWidth,
        clientW: document.documentElement.clientWidth,
        chartH: document.querySelector('#chart').getBoundingClientRect().height,
        tableShown: getComputedStyle(document.querySelector('#rank-table')).display !== 'none',
        cards: document.querySelectorAll('.rank-card').length,
        labelShown: [...document.querySelectorAll('#chart text.star-label')]
          .some(e => getComputedStyle(e).display !== 'none'),
        numsShown: [...document.querySelectorAll('#chart text.dot-num')]
          .filter(e => getComputedStyle(e).display !== 'none').length,
        starShown: [...document.querySelectorAll('#chart path.rec-marker, #chart path.saver-marker')]
          .filter(e => getComputedStyle(e).display !== 'none').length,
        legendShown: getComputedStyle(document.querySelector('#star-legend')).display !== 'none',
        h1H: document.querySelector('h1').getBoundingClientRect().height,
        h1Line: parseFloat(getComputedStyle(document.querySelector('h1')).lineHeight) || 26
      })`).then(JSON.parse);
    check(`手机场景${sc.id} 无横向滚动`, info.scrollW <= info.clientW + 1,
      `${info.scrollW}/${info.clientW}`);
    check(`手机场景${sc.id} 图表高度≥240`, info.chartH >= 240, String(info.chartH.toFixed(1)));
    check(`手机场景${sc.id} 隐藏点旁文字但保留编号点与星标`,
      info.labelShown === false && info.numsShown > 0 && info.starShown > 0 && info.legendShown,
      JSON.stringify({label: info.labelShown, nums: info.numsShown, star: info.starShown, legend: info.legendShown}));
    check(`手机场景${sc.id} 排行改卡片`,
      info.tableShown === false && info.cards > 0 && info.cards <= 5,
      `cards ${info.cards}`);
    check(`手机场景${sc.id} 页头不逐字换行`, info.h1H <= info.h1Line * 1.6,
      `${info.h1H.toFixed(1)} vs line ${info.h1Line}`);
    await evalJs(`(() => { const d = document.querySelector('#chart circle.dot');
        d.dispatchEvent(new MouseEvent('click', {bubbles:true, clientX:200, clientY:300})); })()`);
    await sleep(120);
    const mtip = await evalJs(`JSON.stringify({hidden: document.querySelector('#chart-tip').hidden,
        text: document.querySelector('#chart-tip').textContent})`).then(JSON.parse);
    check(`手机场景${sc.id} 点击点位显示信息`,
      mtip.hidden === false && ["价格", "蛋白质", "脂肪", "热量", "每元蛋白质"].every(k => mtip.text.includes(k)),
      mtip.text.slice(0, 50));
    await evalJs(`document.querySelector('#chart-tip').hidden = true`);
    await shot(sc.id, 390, true);
  }

  await sleep(200);
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
