// B 阶段视觉重做的专项断言（实时服务）。
// Usage: node tests/ui_rework.mjs [url]
//
// 覆盖：统一容器对齐、#data-source、整数刻度、推荐星标坐标、省钱文案、
// 预算内最多（无红色提示框 / 加预算按钮）、整数营养值、SVG 文本不重叠、
// 颜色全部来自 CSS 变量、390px 餐次按钮单行且无横向滚动。
import { spawn } from "node:child_process";
import { waitForJson } from "./cdp_ready.mjs";

const URL_ = process.argv[2] || "http://127.0.0.1:8902/";
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
    `--remote-debugging-port=${PORT}`, "--user-data-dir=/tmp/chrome-cdp-rework",
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

  const load = async (width, height, mobile) => {
    await send("Emulation.setDeviceMetricsOverride", {
      width, height, deviceScaleFactor: mobile ? 2 : 1, mobile,
    });
    await send("Page.navigate", { url: URL_ });
    await waitFor(`document.querySelector('#meta') && document.querySelector('#meta').textContent.includes('门店')`,
      "页面加载");
    await waitFor(`typeof LAND !== 'undefined' && LAND && LAND.items.length > 0`, "性价比图");
  };

  const plan = async (meal, protein, budget, fat = "") => {
    await evalJs(`(() => {
        document.querySelector('#s_meal').value = '${meal}';
        document.querySelector('#s_protein').value = '${protein}';
        document.querySelector('#s_budget').value = '${budget}';
        document.querySelector('#s_fat').value = '${fat}';
        document.querySelector('#s_max_mains').value = '2';
        document.querySelector('#s_tolerance').value = '3';
        document.querySelector('#s_meal').dispatchEvent(new Event('change'));
        document.querySelector('#s_protein').dispatchEvent(new Event('input'));
        document.querySelector('#s_budget').dispatchEvent(new Event('input'));
        document.querySelector('#s_fat').dispatchEvent(new Event('input'));
      })()`);
    await waitFor(`LAND && LAND.meal === '${meal}'`, "切换餐次");
    // 页面打开时会自动算一次默认方案；必须等到「本次请求」的方案回来，
    // 否则可能读到上一次的结果（签名或目标不同）。
    await evalJs(`window.__sig = (typeof S !== 'undefined' && S && S.signature) || ''`);
    await evalJs(`S = null; document.querySelector('#s-plan').click()`);
    await waitFor(`S && S.meal === '${meal}' && S.protein_status
      && Math.abs(Number(S.targets.protein_min) - ${protein}) < 0.01
      && Math.abs(Number(S.targets.budget) - ${budget}) < 0.01
      && S.signature !== window.__sig`, "方案返回");
    await sleep(250);
  };

  // ------------------------------------------------------------- desktop
  await load(1280, 1000, false);

  check("有 #data-source 且状态是 live/cache/offline",
        (await evalJs(`(() => {
            const el = document.querySelector('#data-source');
            return !!el && ['live', 'cache', 'offline'].includes(el.dataset.source);
          })()`)) === true,
        await evalJs(`document.querySelector('#data-source')?.dataset.source`));

  await plan("lunch", 44, 45);

  // 统一容器：输入卡 / 结果卡 / 图表卡 / 页脚左右对齐
  const align = await evalJs(`JSON.stringify((() => {
      const box = (sel) => { const el = document.querySelector(sel);
        return el ? el.getBoundingClientRect() : null; };
      const r = (v) => v === null ? null : Math.round(v * 10) / 10;
      const input = box('.input-card');
      const result = box('#s-result-panel');
      const chart = box('#chart-panel');
      const foot = box('.site-foot');
      const hero = box('.hero h1');
      return { input: r(input?.left), result: r(result?.left),
               chart: r(chart?.left), foot: r(foot?.left), hero: r(hero?.left),
               inputRight: r(input?.right), chartRight: r(chart?.right) };
    })())`).then(JSON.parse);
  check("输入卡/结果卡/图表卡/页脚在同一个容器里",
        align.input !== null && [align.result, align.foot, align.hero].every((v) => Math.abs(v - align.input) <= 1)
        && Math.abs(align.chartRight - align.inputRight) <= 1,
        JSON.stringify(align));

  // 省钱选项文案：与目标比 + 写出省钱方案自己的克数
  const saver = await evalJs(`JSON.stringify({
      text: document.querySelector('#s-result .saver-row')?.textContent || '',
      protein: S.saver ? S.saver.protein : null,
      gap: S.saver ? S.saver.protein_gap : null
    })`).then(JSON.parse);
  check("省钱文案与目标比并写出省钱方案蛋白质",
        /比目标少|同样达标/.test(saver.text)
        && String(saver.protein).replace(/\\.0$/, '') && saver.text.includes(String(saver.protein).replace(/\\.0$/, '')),
        saver.text.replace(/\s+/g, " ").slice(0, 90));

  // 进度条右侧预算信息
  const foot = await evalJs(`document.querySelector('#s-result .progress-foot').textContent`);
  // 套餐纳入后：54 g / 45.00（多 10 g、刚好用完）→ 50 g / 40.50
  // （多 6 g、还剩 4.50 元）。更便宜、仍达标，故更新预期。
  check("进度条写出目标差额与预算余额",
        /目标 44 g，多出 6 g/.test(foot) && /预算 45 元，还剩 4\.50 元/.test(foot),
        foot.replace(/\s+/g, " "));

  // 整数刻度
  const ticks = await evalJs(`JSON.stringify((() => {
      const all = [...document.querySelectorAll('#chart text.tick')];
      const nums = all.map((t) => t.textContent.trim());
      const mono = (arr) => arr.every((t, i) => i === 0 || Number(t) > Number(arr[i - 1]));
      const y = all.filter((t) => t.getAttribute('text-anchor') === 'end').map((t) => t.textContent.trim());
      const x = all.filter((t) => t.getAttribute('text-anchor') === 'middle').map((t) => t.textContent.trim());
      return { all: nums.every((t) => /^\\d+$/.test(t)), monoX: mono(x), monoY: mono(y), count: nums.length };
    })())`).then(JSON.parse);
  check("图表刻度都是整数且单调",
        ticks.all && ticks.monoX && ticks.monoY && ticks.count >= 6, JSON.stringify(ticks));

  // 推荐星标位置 = 组合总价 / 总蛋白
  const rec = await evalJs(`JSON.stringify((() => {
      const el = document.querySelector('#chart .rec-marker');
      if (!el) return null;
      const items = LAND.items;
      const target = Number(document.querySelector('#s_protein').value);
      const budget = Number(document.querySelector('#s_budget').value);
      const xs = items.map((d) => d.price).concat([budget]);
      const ys = items.map((d) => d.protein).concat([target, S.totals.protein]);
      if (S.saver) { xs.push(S.saver.cost); ys.push(S.saver.protein); }
      const xMax = niceTicks(Math.max(...xs), [5, 10]).max;
      const yMax = niceTicks(Math.max(...ys), [5, 10, 15]).max;
      const H = document.querySelector('#chart').viewBox.baseVal.height;
      const X = PAD.l + (Math.min(S.cost, xMax) / xMax) * (CHART_W - PAD.l - PAD.r);
      const Y = H - PAD.b - (Math.min(S.totals.protein, yMax) / yMax) * (H - PAD.t - PAD.b);
      return { got: [+el.dataset.cx, +el.dataset.cy], want: [+X.toFixed(1), +Y.toFixed(1)] };
    })())`).then(JSON.parse);
  check("推荐星标落在（组合总价, 总蛋白）",
        rec && Math.abs(rec.got[0] - rec.want[0]) < 1.5 && Math.abs(rec.got[1] - rec.want[1]) < 1.5,
        JSON.stringify(rec));

  // 整数营养值不出现 .0
  const ints = await evalJs(`JSON.stringify((() => {
      const sels = ['.metric.protein .metric-num', '.blocks .b-value', '.item-sub',
                    '.progress-foot', '.under-note', '.saver-body',
                    '#rank-table td:nth-child(4)', '.item-detail .sub'];
      const bad = [];
      document.querySelectorAll(sels.join(',')).forEach((el) => {
        if (el.closest('.metric.ppy')) return;
        const t = el.textContent.replace(/\\s+/g, ' ');
        if (/\\d+\\.0(?![0-9])/.test(t)) bad.push(t.slice(0, 40));
      });
      return bad;
    })())`).then(JSON.parse);
  check("整数营养值不显示 .0", ints.length === 0, JSON.stringify(ints).slice(0, 120));

  // 颜色全部来自 :root 变量
  const colors = await evalJs(`JSON.stringify((() => {
      const probe = (name, prop) => {
        const d = document.createElement('div');
        d.style[prop] = 'var(' + name + ')';
        document.body.appendChild(d);
        const v = getComputedStyle(d)[prop];
        d.remove();
        return v;
      };
      const bg = (v) => probe(v, 'backgroundColor');
      const fg = (v) => probe(v, 'color');
      const stroke = (v) => probe(v, 'fill');
      const pick = (sel, prop = 'backgroundColor') => {
        const el = document.querySelector(sel);
        return el ? getComputedStyle(el)[prop] : null;
      };
      return {
        card: [pick('.single-card'), bg('--card')],
        brandNum: [pick('.metric.protein .metric-num', 'color'), fg('--brand')],
        primary: [pick('#s-plan'), bg('--brand')],
        dot: [pick('#chart circle.dot:not(.hot)', 'fill'), stroke('--dot-gray')],
        progress: [pick('.progress .fill'), bg('--brand')],
        saveRow: [pick('.saver-row'), bg('--save-bg')],
      };
    })())`).then(JSON.parse);
  const colorOk = Object.values(colors).every(([got, want]) => got && want && got === want);
  check("结果卡/图表/按钮颜色取自 CSS 变量", colorOk, JSON.stringify(colors));

  // SVG 文本：互不重叠、不超出视口（用屏幕坐标换算到 viewBox，旋转过的轴标签才算得准）
  const texts = await evalJs(`JSON.stringify((() => {
      const svg = document.querySelector('#chart');
      const vb = svg.viewBox.baseVal;
      const sr = svg.getBoundingClientRect();
      const sx = vb.width / sr.width;
      const sy = vb.height / sr.height;
      const boxes = [...svg.querySelectorAll('text')]
        .filter((t) => getComputedStyle(t).display !== 'none')
        .map((t) => {
          const r = t.getBoundingClientRect();
          return { t: t.textContent.trim().slice(0, 14),
                   l: (r.left - sr.left) * sx, r: (r.right - sr.left) * sx,
                   top: (r.top - sr.top) * sy, bot: (r.bottom - sr.top) * sy,
                   cls: t.getAttribute('class') || '' };
        });
      const outside = boxes.filter((b) => b.l < -0.5 || b.r > vb.width + 0.5
        || b.top < -0.5 || b.bot > vb.height + 0.5);
      const tightRight = boxes.filter((b) => b.r > vb.width - 16 + 0.5 && b.cls.includes('star-label'));
      const overlaps = [];
      for (let i = 0; i < boxes.length; i += 1) {
        for (let j = i + 1; j < boxes.length; j += 1) {
          const a = boxes[i]; const b = boxes[j];
          const hit = !(a.r + 1 < b.l || a.l - 1 > b.r || a.bot + 1 < b.top || a.top - 1 > b.bot);
          if (hit) overlaps.push([a.t, b.t]);
        }
      }
      return { n: boxes.length, outside, tightRight, overlaps };
    })())`).then(JSON.parse);
  check("SVG 文字互不重叠且不超出视口",
        texts.outside.length === 0 && texts.overlaps.length === 0 && texts.tightRight.length === 0,
        JSON.stringify({ n: texts.n, outside: texts.outside.map((b) => b.t),
                         overlaps: texts.overlaps.slice(0, 3), right: texts.tightRight.map((b) => b.t) }));

  // ------------------------------------------------- 预算内最多：没有红框
  await plan("lunch", 35, 25);
  const under = await evalJs(`JSON.stringify({
      text: document.querySelector('#s-result').textContent,
      violations: document.querySelectorAll('#s-result .violations, #s-result .v').length,
      cards: document.querySelectorAll('#s-result .under-card').length,
      note: !!document.querySelector('#s-result .under-note'),
      budgetBtn: !!document.querySelector('#s-budget-up'),
      foot: document.querySelector('#s-result .progress-foot').textContent
    })`).then(JSON.parse);
  check("预算内最多：黑框卡片 + 浅灰说明 + 无红色提示框",
        under.violations === 0 && under.cards >= 1 && under.note && under.budgetBtn
        && under.text.includes("最多只能做到") && !under.text.includes("当前条件下没有满足全部要求的方案"),
        JSON.stringify({ v: under.violations, cards: under.cards, note: under.note }));
  check("预算内最多：进度条写明还差多少并标注超出容差",
        /还差 7 g/.test(under.foot) && /超出允许的 3 g/.test(under.foot),
        under.foot.replace(/\s+/g, " "));
  check("预算内最多：页面没有红/粉色边框或底色",
        (await evalJs(`(() => {
            const probe = (name, prop) => {
              const d = document.createElement('div');
              d.style[prop] = 'var(' + name + ')';
              document.body.appendChild(d);
              const v = getComputedStyle(d)[prop];
              d.remove();
              return v;
            };
            const brand = probe('--brand', 'color');
            const soft = probe('--brand-soft', 'backgroundColor');
            const bad = [];
            document.querySelectorAll('#s-result *').forEach((el) => {
              if (el.closest('.qty')) return;               // 商品行的浅红方块是设计的一部分
              const s = getComputedStyle(el);
              [s.backgroundColor, s.borderTopColor, s.borderLeftColor].forEach((c) => {
                if (c === brand || c === soft) bad.push((el.className || el.tagName) + ':' + c);
              });
            });
            return bad.length;
          })()`)) === 0);

  // 按加预算卡片给的上限重新算（套餐让上限从 39.00 降到 34.50）
  const upCost = await evalJs(`document.querySelector('#s-budget-up').dataset.cost`);
  await evalJs(`document.querySelector('#s-budget-up').click()`);
  await waitFor(`document.querySelector('#s_budget').value === '${upCost}'`, `价格改成 ${upCost}`);
  await waitFor(`S && S.protein_status === 'met' && S.cost <= ${upCost}`, "按上限重算");
  const redo = await evalJs(`JSON.stringify({
      budget: document.querySelector('#s_budget').value,
      status: S.protein_status, cost: S.cost,
      panelShown: !document.querySelector('#s-result-panel').hidden
    })`).then(JSON.parse);
  check("点「重新算」把价格改成卡片上限并重新出结果",
        redo.budget === upCost && redo.status === "met" && redo.cost <= Number(upCost) && redo.panelShown,
        JSON.stringify(redo));

  // --------------------------------------------------------------- mobile
  await load(390, 844, true);
  await plan("lunch", 44, 45);

  const mob = await evalJs(`JSON.stringify({
      scrollW: document.documentElement.scrollWidth,
      clientW: document.documentElement.clientWidth,
      seg: [...document.querySelectorAll('#meal-seg button')].map((b) => {
        const r = document.createRange(); r.selectNodeContents(b);
        const rects = [...r.getClientRects()];
        return { text: b.textContent, h: +b.getBoundingClientRect().height.toFixed(1),
                 lines: rects.length,
                 textH: +Math.max(...rects.map((x) => x.height)).toFixed(1) };
      }),
      inputRow: (() => {
        const h = (sel) => Math.round(document.querySelector(sel).getBoundingClientRect().top);
        return { meal: h('.field.meal'), protein: h('#s_protein'), budget: h('#s_budget'),
                 btn: h('#s-plan') };
      })(),
      wrap: [...document.querySelectorAll('.input-card, #s-result-panel, #chart-panel, .site-foot')]
        .map((el) => Math.round(el.getBoundingClientRect().left))
    })`).then(JSON.parse);
  check("390px 无横向滚动", mob.scrollW <= mob.clientW + 1, `${mob.scrollW}/${mob.clientW}`);
  check("390px 餐次按钮单行且高度 ≤48",
        mob.seg.every((b) => b.lines === 1 && b.h <= 48 && b.textH <= 26),
        JSON.stringify(mob.seg));
  check("390px 餐次独占一行、输入并排、按钮整行",
        mob.inputRow.meal < mob.inputRow.protein
        && Math.abs(mob.inputRow.protein - mob.inputRow.budget) <= 2
        && mob.inputRow.btn > mob.inputRow.protein,
        JSON.stringify(mob.inputRow));
  check("390px 卡片与页脚左边缘对齐",
        Math.max(...mob.wrap) - Math.min(...mob.wrap) <= 1, JSON.stringify(mob.wrap));

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
