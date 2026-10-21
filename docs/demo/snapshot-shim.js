/* Static "backend" for the GitHub Pages demo.
 *
 * web/app.js talks to /api/*; this shim answers those calls from snapshot.json
 * with web/solver.js, so the page keeps the exact same renderer without a server.
 * Nothing here touches the network except loading the snapshot once.
 */
(function () {
  "use strict";

  var SNAP = null;
  var READY = fetch("demo/snapshot.json").then(function (r) { return r.json(); })
    .then(function (data) {
      SNAP = data;
      return data;
    });

  var realFetch = window.fetch ? window.fetch.bind(window) : null;
  window.fetch = function (input, init) {
    var url = typeof input === "string" ? input : input.url;
    if (url.indexOf("/api/") !== 0) {
      if (realFetch) return realFetch(input, init);
      return Promise.reject(new Error("offline"));
    }
    return READY.then(function () {
      var body = {};
      if (init && init.body) {
        try { body = JSON.parse(init.body); } catch (e) { body = {}; }
      }
      var payload = handle(url, body);
      var status = payload && payload.__status ? payload.__status : 200;
      if (payload) delete payload.__status;
      return {
        ok: status >= 200 && status < 300,
        status: status,
        json: function () { return Promise.resolve(payload); },
      };
    });
  };

  function store() {
    return {
      code: SNAP.meta.store.code,
      name: SNAP.meta.store.name,
      beType: 1,
      orderType: 1,
    };
  }

  function catalog() {
    var rows = 0;
    Object.keys(SNAP.meals).forEach(function (meal) {
      var m = SNAP.meals[meal];
      rows += m.mains.length + m.drinks.length + m.sides.length;
    });
    return { rows: rows, unique_names: rows, aliases: 0, duplicates: 0 };
  }

  function bootstrap() {
    return {
      state: {
        store: store(),
        date: SNAP.meta.date,
        meal_times: SNAP.meta.meal_times,
      },
      cache: {
        source: "snapshot",
        date: SNAP.meta.date,
        fetched_at: SNAP.meta.fetched_at,
        meal_times: SNAP.meta.meal_times,
        menu_warnings: {},
      },
      source: "snapshot",
      fetched_at: SNAP.meta.fetched_at,
      catalog: catalog(),
      unknown: { breakfast: [], lunch: [], dinner: [] },
      baseline: null,
    };
  }

  function landscape(meal) {
    var land = SNAP.landscape[meal] || {};
    return {
      meal: meal,
      meal_label: (SNAP.meals[meal] || {}).label || meal,
      items: land.items || [],
      frontier: land.frontier || [],
      max_mains: 2,
      basis: "official_standard",
      note: "快照数据：仅官方标准版整款数据。",
    };
  }

  function suggestionList(items) {
    var out = [];
    items.forEach(function (item) {
      if (item.kind !== "main") return;
      (item.removable || []).forEach(function (name) {
        var risky = /芝士|吉士|干酪|培根|蛋/.test(name);
        out.push({
          code: name,
          name: name,
          target_code: item.code,
          target_name: item.name,
          muscle_warning: risky,
          muscle_note: risky ? "增肌时不建议去掉，会减少蛋白质" : "",
          protein_risk: risky,
          default_suggestion: true,
        });
      });
    });
    return out;
  }

  function upgradesFor(items) {
    var mains = items.filter(function (i) { return i.kind === "main"; });
    var out = [];
    (SNAP.ladder || []).forEach(function (pair) {
      var from = null;
      Object.keys(SNAP.meals).forEach(function (meal) {
        SNAP.meals[meal].mains.forEach(function (row) {
          if (row.code === pair.from) from = row;
        });
      });
      if (!from) return;
      var hit = mains.find(function (i) { return i.code === pair.from; });
      if (!hit) return;
      var to = null;
      Object.keys(SNAP.meals).forEach(function (meal) {
        SNAP.meals[meal].mains.forEach(function (row) {
          if (row.code === pair.to) to = row;
        });
      });
      if (!to) return;
      var dp = Math.round((to.nutrition.protein - from.nutrition.protein) * 10) / 10;
      var dprice = Math.round((to.price - from.price) * 100) / 100;
      var dfat = Math.round(((to.nutrition.fat || 0) - (from.nutrition.fat || 0)) * 10) / 10;
      out.push({
        code: pair.to,
        name: to.name,
        label: pair.label,
        from_code: pair.from,
        to_code: pair.to,
        delta_protein: dp,
        delta_price: dprice,
        delta_fat: dfat,
        evidence: pair.evidence,
        show_conversion: dp >= 5 && dprice > 0,
        protein_per_extra_yuan: dprice > 0 ? Math.round((dp / dprice) * 10) / 10 : null,
        fits: true,
        fit_note: "快照未复算整单，仅作整款替换参考",
      });
    });
    return out;
  }

  function mealPlan(body) {
    var result = window.McdSolver.solve(SNAP, body);
    var items = result.items.map(function (i) {
      return {
        code: i.code,
        name: i.name,
        kind: i.kind,
        quantity: i.quantity,
        price_ref: i.price_ref,
        nutrition: i.nutrition,
        removable: i.removable || [],
        nutrition_basis: "standard",
        nutrition_status: "official",
      };
    });
    var saver = result.saver ? {
      items: result.saver.items.map(function (i) {
        return i.quantity === 1 ? i.name : i.name + " ×" + i.quantity;
      }),
      protein: result.saver.protein,
      fat: result.saver.fat,
      kcal: result.saver.kcal,
      cost: result.saver.cost,
      protein_less: result.saver.protein_less,
      money_saved: result.saver.money_saved,
      protein_gap: result.saver.protein_gap,
      protein_per_yuan: result.saver.protein_per_yuan,
    } : null;

    return {
      feasible: result.feasible,
      mode: "single",
      meal: body.meal,
      meal_label: (SNAP.meals[body.meal] || {}).label || body.meal,
      items: items,
      totals: result.totals,
      cost: result.cost,
      effective_cost: result.cost,
      money_basis: "snapshot",
      budget_status: "snapshot",
      protein_per_yuan: result.protein_per_yuan,
      protein_status: result.protein_status,
      protein_gap: result.protein_gap,
      protein_tolerance: result.targets.protein_tolerance,
      protein_accept_min: result.protein_accept_min,
      saver: saver,
      suggestions: suggestionList(items),
      upgrades: upgradesFor(items),
      upgrade: upgradesFor(items)[0] || null,
      frontier: result.frontier,
      context_warning: "",
      signature: "snapshot",
      strict_mode: true,
      nutrition_basis: "official_standard",
      targets: result.targets,
      note: result.note || "",
      quotes: {},
    };
  }

  function handle(url, body) {
    var path = url.split("?")[0];
    if (path === "/api/bootstrap") return bootstrap();
    if (path === "/api/meal/landscape") {
      var meal = (url.split("meal=")[1] || "lunch").split("&")[0];
      return landscape(meal);
    }
    if (path === "/api/meal") return mealPlan(body);
    if (path === "/api/recompute") return mealPlan(body && body.plan ? body.plan : { meal: "lunch", protein_min: 40, budget: 45 });
    if (path === "/api/quote") {
      return { status: "unsupported", amount: null, message: "在线版不连接门店，无法试算。", __status: 200 };
    }
    if (path === "/api/settings" || path === "/api/refresh") {
      return { date: SNAP.meta.date, source: "snapshot", fetched_at: SNAP.meta.fetched_at };
    }
    return { error: "not found", __status: 404 };
  }
}());
