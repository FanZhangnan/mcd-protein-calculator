/* Single-meal solver for the static demo.
 *
 * Mirrors src/planner.py + Service.plan_meal: the snapshot already holds every
 * structure-legal combo, so this file only filters, ranks and derives the
 * fallback / saver / frontier rows. No DOM, no network.
 *
 * UMD so the same file runs in the browser and in the parity test (node).
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.McdSolver = factory();
}(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var KEYS = ["energyKj", "energyKcal", "protein", "fat", "carbohydrate", "sodium", "calcium"];
  var EPS = 1e-9;

  function round(v, d) {
    var f = Math.pow(10, d);
    return Math.round((v + Number.EPSILON) * f) / f;
  }

  function expand(mealData, row) {
    var picks = [];
    for (var i = 0; i < 3; i++) {
      var idx = row[i];
      if (idx >= 0) picks.push(idx);
    }
    var items = [];
    var seen = {};
    picks.forEach(function (idx) {
      if (seen[idx] === undefined) {
        seen[idx] = items.length;
        items.push({ row: mealData.mains[idx], kind: "main", quantity: 1 });
      } else {
        items[seen[idx]].quantity += 1;
      }
    });
    var side = row[3];
    if (side >= 0) items.push({ row: mealData.sides[side], kind: "side", quantity: 1 });
    var drink = row[4];
    if (drink >= 0) items.push({ row: mealData.drinks[drink], kind: "drink", quantity: 1 });

    return items.map(function (it) {
      var n = it.row.nutrition || {};
      return {
        code: it.row.code,
        name: it.row.name,
        kind: it.kind,
        quantity: it.quantity,
        price_ref: it.row.price,
        nutrition: n,
        removable: it.row.removable || [],
      };
    });
  }

  // Combo row: [comboIdx, main1, main2, cost, protein, fat, kcal]
  function expandCombo(mealData, row) {
    var variant = mealData.comboVariants[row[0]];
    var items = [{
      code: variant.code,
      name: variant.name,
      kind: "combo",
      quantity: 1,
      price_ref: variant.price,
      nutrition: variant.nutrition,
      removable: [],
      combo: { variant: variant.variant, rounds: variant.rounds, menu_price: variant.menu_price,
               replacements: variant.replacements || [] },
    }];
    for (var i = 1; i <= 2; i++) {
      var idx = row[i];
      if (idx >= 0) {
        var m = mealData.mains[idx];
        items.push({ code: m.code, name: m.name, kind: "main", quantity: 1,
          price_ref: m.price, nutrition: m.nutrition, removable: m.removable || [] });
      }
    }
    return items;
  }

  function totalsOf(items) {
    var totals = {};
    var unknown = false;
    KEYS.forEach(function (key) {
      var sum = 0;
      var bad = false;
      items.forEach(function (it) {
        var v = it.nutrition ? it.nutrition[key] : null;
        if (v === null || v === undefined) bad = true;
        else sum += Number(v) * it.quantity;
      });
      if (bad || !items.length) { totals[key] = null; unknown = true; }
      else totals[key] = round(sum, 1);
    });
    return { totals: totals, unknown: unknown };
  }

  function costOf(items) {
    var sum = 0;
    for (var i = 0; i < items.length; i++) {
      if (items[i].price_ref === null || items[i].price_ref === undefined) return null;
      sum += Number(items[i].price_ref) * items[i].quantity;
    }
    return round(sum, 2);
  }

  function limits(q) {
    return {
      budget: q.budget === undefined || q.budget === null || q.budget === "" ? null : Number(q.budget),
      fat_max: q.fat_max === undefined || q.fat_max === null || q.fat_max === "" ? null : Number(q.fat_max),
      kcal_min: q.kcal_min === undefined || q.kcal_min === null || q.kcal_min === "" ? null : Number(q.kcal_min),
      kcal_max: q.kcal_max === undefined || q.kcal_max === null || q.kcal_max === "" ? null : Number(q.kcal_max),
    };
  }

  function capsOk(totals, cost, lim) {
    if (cost === null) return false;
    if (lim.budget !== null && cost > lim.budget + EPS) return false;
    if (lim.fat_max !== null && (totals.fat === null || totals.fat > lim.fat_max + EPS)) return false;
    if (lim.kcal_min !== null || lim.kcal_max !== null) {
      if (totals.energyKcal === null) return false;
      var low = lim.kcal_min === null ? 0 : lim.kcal_min;
      var high = lim.kcal_max === null ? 1e9 : lim.kcal_max;
      if (totals.energyKcal < low - EPS || totals.energyKcal > high + EPS) return false;
    }
    return true;
  }

  function feasible(totals, cost, lim, proteinMin) {
    if (totals.protein === null) return false;
    if (lim.fat_max !== null && totals.fat === null) return false;
    if ((lim.kcal_min !== null || lim.kcal_max !== null) && totals.energyKcal === null) return false;
    if (lim.budget !== null && cost === null) return false;
    if (totals.protein < proteinMin - EPS) return false;
    return capsOk(totals, cost, lim);
  }

  function betterPrice(a, b) {
    if (a.cost !== b.cost) return a.cost < b.cost;
    if (a.totals.protein !== b.totals.protein) return a.totals.protein > b.totals.protein;
    return a.totals.fat < b.totals.fat;
  }

  function betterProtein(a, b) {
    if (a.totals.protein !== b.totals.protein) return a.totals.protein > b.totals.protein;
    if (a.cost !== b.cost) return a.cost < b.cost;
    return a.totals.fat < b.totals.fat;
  }

  function build(mealData, row, lim) {
    var items = expand(mealData, row);
    var t = totalsOf(items);
    return { items: items, totals: t.totals, cost: costOf(items), row: row };
  }

  function brief(row) {
    if (!row) return null;
    var head = row.items.find(function (i) { return i.kind === "combo"; })
      || row.items.find(function (i) { return i.kind === "main"; });
    return {
      code: head ? head.code : "",
      name: head ? head.name : "",
      protein: row.totals.protein === null ? null : round(row.totals.protein, 1),
      fat: row.totals.fat === null ? null : round(row.totals.fat, 1),
      kcal: row.totals.energyKcal === null ? null : round(row.totals.energyKcal, 1),
      cost: row.cost === null ? null : round(row.cost, 2),
      items: row.items.map(function (i) {
        return i.quantity === 1 ? i.name : i.name + " ×" + i.quantity;
      }),
    };
  }

  function frontier(rows, lim, target) {
    var hard = rows.filter(function (r) { return capsOk(r.totals, r.cost, lim); });
    var best = null;
    hard.forEach(function (r) { if (!best || betterProtein(r, best)) best = r; });

    var reachable = rows.some(function (r) { return r.totals.protein >= target - EPS; });

    function otherOk(r, opts) {
      opts = opts || {};
      if (!opts.relaxFat && lim.fat_max !== null) {
        if (r.totals.fat === null || r.totals.fat > lim.fat_max + EPS) return false;
      }
      if (!opts.relaxPrice && lim.budget !== null) {
        if (r.cost > lim.budget + EPS) return false;
      }
      if (!opts.relaxKcal && (lim.kcal_min !== null || lim.kcal_max !== null)) {
        var low = lim.kcal_min === null ? 0 : lim.kcal_min;
        var high = lim.kcal_max === null ? 1e9 : lim.kcal_max;
        if (r.totals.energyKcal === null
          || r.totals.energyKcal < low - EPS || r.totals.energyKcal > high + EPS) return false;
      }
      return true;
    }

    var reach = rows.filter(function (r) {
      return r.totals.protein >= target - EPS && otherOk(r, { relaxPrice: true });
    });
    var reachRow = null;
    reach.forEach(function (r) {
      if (!reachRow || r.cost < reachRow.cost
        || (r.cost === reachRow.cost && (r.totals.fat || 0) < (reachRow.totals.fat || 0))) reachRow = r;
    });

    var relaxFatRow = null;
    if (lim.fat_max !== null) {
      var fatRows = rows.filter(function (r) {
        return r.totals.protein >= target - EPS && otherOk(r, { relaxFat: true });
      });
      fatRows.forEach(function (r) {
        var f = r.totals.fat === null ? 1e6 : r.totals.fat;
        var bf = relaxFatRow ? (relaxFatRow.totals.fat === null ? 1e6 : relaxFatRow.totals.fat) : null;
        if (!relaxFatRow || f < bf || (f === bf && r.cost < relaxFatRow.cost)) relaxFatRow = r;
      });
    }

    var message = "";
    if (!reachable) {
      var top = null;
      rows.forEach(function (r) { if (!top || r.totals.protein > top.totals.protein) top = r; });
      message = "这家店当前菜单达不到 " + target + " g 蛋白质"
        + (top ? "（整份最高 " + Math.round(top.totals.protein) + " g，" + top.items[0].name + "）" : "");
    } else if (!best) {
      message = "当前价格/脂肪/热量限制下没有任何可选组合";
    }

    return {
      max_protein: best ? brief(best) : null,
      reach_price: reachRow ? brief(reachRow) : null,
      relax_fat: relaxFatRow ? brief(relaxFatRow) : null,
      relax_kcal: null,
      reachable: reachable,
      candidates: rows.length,
      message: message,
    };
  }

  function solve(snapshot, q) {
    var meal = q.meal || "lunch";
    var data = snapshot.meals[meal];
    if (!data) throw new Error("unknown meal: " + meal);

    var lim = limits(q);
    var tolerance = Number(q.protein_tolerance === undefined ? 3 : q.protein_tolerance) || 0;
    tolerance = Math.max(0, Math.min(10, tolerance));
    var target = Number(q.protein_min);
    var acceptMin = Math.max(0.1, target - tolerance);
    var maxMains = Number(q.max_mains || 2);
    var allowSide = !!q.allow_side;

    var rows = [];
    data.combos.forEach(function (raw) {
      var size = (raw[0] >= 0 ? 1 : 0) + (raw[1] >= 0 ? 1 : 0) + (raw[2] >= 0 ? 1 : 0);
      if (size > maxMains) return;
      if (!allowSide && raw[3] >= 0) return;
      rows.push(build(data, raw, lim));
    });
    (data.comboCombos || []).forEach(function (raw) {
      var mains = (raw[1] >= 0 ? 1 : 0) + (raw[2] >= 0 ? 1 : 0);
      if (mains + 1 > maxMains) return;   // the combo itself counts as one main
      var items = expandCombo(data, raw);
      var t = totalsOf(items);
      rows.push({ items: items, totals: t.totals, cost: costOf(items), row: raw });
    });

    var strict = rows.filter(function (r) { return feasible(r.totals, r.cost, lim, target); });
    var accepted = rows.filter(function (r) { return feasible(r.totals, r.cost, lim, acceptMin); });
    var main = null;
    if (strict.length) {
      strict.forEach(function (r) { if (!main || betterPrice(r, main)) main = r; });
    } else {
      accepted.forEach(function (r) { if (!main || betterPrice(r, main)) main = r; });
    }

    var capped = rows.filter(function (r) { return capsOk(r.totals, r.cost, lim); });
    var card = main;
    if (!card && capped.length) {
      capped.forEach(function (r) { if (!card || betterProtein(r, card)) card = r; });
    }

    if (!card) {
      return {
        feasible: false, items: [], totals: {}, cost: 0, protein_status: "under",
        protein_gap: null, protein_per_yuan: null, saver: null,
        frontier: frontier(rows, lim, target),
        note: "没有任何组合满足全部硬约束，不提供商品卡片。",
        targets: { protein_min: target, budget: lim.budget, fat_max: lim.fat_max,
          kcal_min: lim.kcal_min, kcal_max: lim.kcal_max, protein_tolerance: tolerance },
      };
    }

    var protein = card.totals.protein;
    var met = strict.length > 0 && protein !== null && protein + EPS >= target;
    var status = met ? "met" : (accepted.length ? "near" : "under");
    var gap = protein === null ? null : round(Math.max(0, target - protein), 1);
    var perYuan = protein !== null && card.cost > 0 ? round(protein / card.cost, 1) : null;

    var saver = null;
    if (strict.length && tolerance > 0) {
      var cheap = null;
      accepted.forEach(function (r) {
        if (!cheap || betterPrice(r, cheap)) cheap = r;
      });
      if (cheap && cheap.cost < main.cost - EPS) {
        var mainProtein = main.totals.protein || 0;
        saver = {
          items: cheap.items.map(function (i) {
            return { code: i.code, name: i.name, quantity: i.quantity, kind: i.kind,
              price_ref: i.price_ref, nutrition: i.nutrition };
          }),
          protein: round(cheap.totals.protein, 1),
          fat: round(cheap.totals.fat || 0, 1),
          kcal: round(cheap.totals.energyKcal || 0, 1),
          cost: round(cheap.cost, 2),
          protein_less: round(Math.max(0, mainProtein - cheap.totals.protein), 1),
          money_saved: round(Math.max(0, main.cost - cheap.cost), 2),
          protein_gap: round(Math.max(0, target - cheap.totals.protein), 1),
          protein_per_yuan: cheap.cost > 0 ? round(cheap.totals.protein / cheap.cost, 1) : null,
        };
      }
    }

    return {
      feasible: strict.length > 0,
      items: card.items,
      totals: card.totals,
      cost: round(card.cost, 2),
      protein_status: status,
      protein_gap: gap,
      protein_per_yuan: perYuan,
      saver: saver,
      frontier: accepted.length ? null : frontier(rows, lim, target),
      note: strict.length ? "" : "当前条件下没有满足全部要求的方案，以下为预算内蛋白质最高的组合。",
      targets: { protein_min: target, budget: lim.budget, fat_max: lim.fat_max,
        kcal_min: lim.kcal_min, kcal_max: lim.kcal_max, protein_tolerance: tolerance },
      protein_accept_min: round(acceptMin, 2),
    };
  }

  return { solve: solve, expand: expand, totalsOf: totalsOf, costOf: costOf, round: round };
}));
