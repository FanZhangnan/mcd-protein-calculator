---
name: mcd-protein-calculator
description: 麦当劳增肌计算器。在「这一顿至少吃多少克蛋白质、最多花多少钱」下，用麦当劳 MCP 的实时菜单、门店价格与官方营养，枚举所有结构合法的组合，给出最便宜的达标方案、每元蛋白质、无解时的预算内蛋白质上限与逐项放宽建议。当用户问麦当劳这一餐怎么点最划算、多少钱能吃到多少蛋白质、增肌怎么点、或查询可去除配料与肉量升级时使用。
---

# 麦当劳增肌计算器

本仓库已实现并验证。**先运行，不要重新造轮子。** 下文的路径都相对于仓库根目录。

## 何时用

- 「午餐想吃到 40 g 蛋白质，最多 45 元，怎么点？」
- 「25 元以内在麦当劳最多能吃到多少蛋白质？」
- 「麦当劳增肌怎么点最划算？」
- 「这个汉堡能去掉什么？去掉后蛋白质会变吗？」
- 「麦香鱼加钱换双层鳕鱼堡，多花 3 元值不值？」

## 运行方式与数据隔离

```bash
python3 src/server.py                 # 默认 http://127.0.0.1:8765
python3 src/server.py --port 8780     # 端口被占用时，后续地址跟着换
python3 src/server.py --offline       # 无网络时用 fixture，来源会标 offline
```

- 想换运行数据目录时设置 `MAI_MAI_DATA=<目录>/data`，不要覆盖别人的 `data/`。
- 启动前先查端口占用。**占用者不是自己启动的进程就不要停、不要复用**，换一个确认空闲的端口，并在本轮所有 URL 里同步。
- `GET /api/bootstrap` 首次请求要等实时菜单与营养查询，短超时不等于服务没起。

## 铁律（先读这三条）

1. **只用用户说过的约束。** 蛋白质下限、价格上限必填；脂肪上限、热量上下限、配菜、主食数量只在用户明确提到时才传。**不要照抄下面示例里的可选字段**——多传一个 `fat_max: 40` 就会把脂肪 44 g 的组合排除掉，答案和用户问的不是同一道题。
2. **所有数字来自接口返回**，不自己估算营养或价格；接口没给的，就说没有。对接口字段做加减（45 − 39 = 还剩 6 元、42 − 34 = 多 8 g）可以，但不能按份量自己推算营养。
3. **营养只用官方标准版整款数据**；去料只写点单参数，不扣减数值。

### 蛋白质目标容差（服务端统一计算）

- `/api/meal` 支持 `protein_tolerance`（单位 g，默认 **3**，只接受 0–10 的整数）。**不要再自己用「目标 − 3」当 `protein_min` 求解**，把原始目标和容差分开传，网页与对话共用同一套逻辑。
- 返回的 `protein_status` 只有三种：`met`（达标）、`near`（接近达标）、`under`（预算内最多）。`near` 时用 `protein_gap` 说明「比目标少 X g，在你允许的 t g 内」，**不能把 42 g 写成目标值**。
- 原目标有解时，如果还存在更便宜的容差组合，返回 `saver`：`items`（完整组合）、`protein`、`cost`、`protein_less`（比主方案少几克）、`money_saved`（省几元）。**两个方案都要讲清楚，由用户选择。**
- 用户选择省钱方案时，用同一份输入加 `"select":"saver"` 重新调用 `/api/meal`（不要自己改 `protein_min`，也不要本地拼组合），再用新返回的 `signature` 报价。
- `under` 时放宽方案仍按**原始目标**计算；`protein_tolerance` 不会放宽价格、脂肪或热量上限。

## 产品规则（不可擅自放宽）

1. 主食 1–N 个（N 默认 2，可设 1）；同一款 2 个合并为一行、数量 2。
2. 饮品 0–1 杯，仅限白名单（纯牛奶、零度可乐、美式咖啡，或官方热量 < 20 kcal）。
3. 配菜 0–1 份，且用户必须显式允许。
4. 硬约束：价格上限；用户填写的脂肪/热量上下限。**无解时的主卡片也不能突破。**
5. 排序：先筛「蛋白质达标 + 全部硬约束满足」，再按价格最低 → 蛋白质最高 → 脂肪最低。
6. 每元蛋白质 = 蛋白质 g ÷ 价格 元，保留一位小数；候选全量枚举。
7. 营养只用官方标准版整款数据；去料只写点单参数，不扣减数值。

### 无解时

- 第一行固定：「当前条件下没有满足全部要求的方案」。
- 主卡片 = 硬约束内蛋白质最高的组合，标签「预算内最多」，显示蛋白质缺口。
- 若没有任何组合满足硬约束，不显示商品卡片。
- 放宽方案只作文字：只放宽价格需要最低多少钱；设了脂肪/热量上限时，只放宽这一项需要放宽到多少。

## 怎么调用

### 0. 先确认服务在跑

```bash
curl -s localhost:8765/api/bootstrap
```

没有响应再启动，**不要重复启动**。

### 1. 求解这一餐

```bash
curl -s -X POST localhost:8765/api/meal -H 'Content-Type: application/json' \
  -d '{"meal":"lunch","protein_min":40,"budget":45}'
```

可选字段只在用户明确提到时才加：`fat_max`、`kcal_min`、`kcal_max`、`allow_side`、`max_mains`（默认 2）。`protein_tolerance` 不传就是默认 3；用户明确说「要严格达标」或给了别的偏差时才传 0–10。

关键返回：`feasible`、`protein_status`、`protein_gap`、`saver`、`items[]`、`totals`、`cost`、`protein_per_yuan`、`signature`、`suggestions[]`、`upgrades[]`、`frontier`、`context_warning`、`removable`、`removables`。

### 1.1 套餐（`kind = "combo"`）

`/api/meal` 的 `items[]` 里可能出现 `kind = "combo"` 的一行：套餐作为一个整体参与求解，`combo.rounds` 是各轮选中的内容（主食 / 小食 / 饮品），`round_list` 是报价要回传的轮次参数。

- 结构规则由服务端保证：**一餐最多 1 个套餐**，套餐计 1 个主食；有套餐时不再额外加饮品或配菜。不要自己在对话里拼「套餐 + 另点一杯饮料」。
- 套餐营养 = 各轮官方单品营养之和；价格 = 门店真实试算价。**不要用菜单价或各轮 diffPrice 自己加减**。
- `combos.catalog` 是核查记录：`variants_used` 纳入的变体数、`excluded` 与 `reasons`（`nutrition_unknown` / `price_mismatch` / `price_failed` / `membership`）。用户问「为什么没算某个套餐」时，先查这里再回答。
- 会员价套餐（早餐卡 / 麦金卡）**不进入候选**，因为价格依赖账号权益；不要拿菜单会员价和普通价混着比较。
- 套餐暂不支持去配料与券后价：`removable.reason` 会是「套餐暂不支持去配料」，`/api/quote_with_coupon` 返回 `status = "unsupported"`。直接说「套餐暂不支持」，不要报错或自行估算券后价。
- 报价仍用 `/api/quote`，服务端会把 `roundList` 一起发出去；**不要把套餐拆成单品分别报价**。
- 性价比图与排行表只画单品主食，不画套餐；不要因此说套餐不在计算范围内。

### 2. 性价比图

```bash
curl -s "localhost:8765/api/meal/landscape?meal=lunch&max_mains=2"
```

`items[]` 每元蛋白质降序；`frontier[]` 是「价格 ≤ B 时最多多少蛋白质」的阶梯。

### 3. 为当前方案报价

```bash
curl -s -X POST localhost:8765/api/quote -H 'Content-Type: application/json' \
  -d '{"signature":"<当前方案最新的 signature>"}'
```

不重新选餐。**始终用最新返回的签名**：`/api/meal`、`/api/recompute` 都返回当前 `signature`，其值可能随方案变化，不能沿用旧变量；过期签名返回 `409 stale_signature`，门店/日期/时段变了返回 `409 stale_context`。

报价只返回组合总价时，只有该总价可以称为「试算价」。`items[].price_ref` 等单品字段仍是参考价，必须单独标注，不能把它们说成去料后的逐项实时报价。按参考价相加得到的新组合金额也只是派生参考金额，未经 `/api/quote` 不能称为已报价。

### 4. 去料（只能减，不能加）

```bash
curl -s -X POST localhost:8765/api/recompute -H 'Content-Type: application/json' \
  -d '{"customizations":{"lunch":{"1120":{"action":"suggest_remove","option_codes":["100141"]}}}}'
```

- 可去项 code 只能取自 `/api/meal` 的 `suggestions[]`，或 `GET /api/removable?code=1120&meal=lunch` 的 `options[]`。
- 只有 `suggest_remove`；**任何加料动作（add / add_patty / extra_patty）都会 400 `unsupported_customization`**。
- 去料后**条目级** `items[].nutrition_basis` 变成 `standard_unadjusted`（顶层没有这个字段），营养数值仍是标准版；`feasible` 也仍按标准版判定。回复用户时必须把这一点讲清楚，用「可能低于显示值」的原口径，不要说成「一定更低」。
- **不能把标准版达标说成定制后实际达标，也不能猜实际值在目标附近。** 例如接口仍返回标准版 42 g、用户目标为 40 g 时，应说：「显示的 42 g 是官方标准版参考值；本工具没有扣减芝士营养，因此无法确认去芝士后的实际蛋白质是否仍达到 40 g。」不得说「实际可能贴着 40 g 边缘」「去掉后仍够 40 g」或自行给出减少量、区间。所谓「营养不变」只指接口保留的显示值，不代表实际成品营养不变。

### 5. 肉量升级（整款换整款）

```bash
curl -s localhost:8765/api/ladder
```

返回 `pairs[]`：`from` / `to` / `label` / `evidence`，只包含肉饼（鸡排、鱼排）数量真正增加的配对。价格和蛋白质用 `/api/meal/landscape` 或方案 `items[]` 里同一 `code` 的数值，不要自己算。

用户问「能加一块肉饼吗」：

- 明确说不支持追加配料，接口只允许去料；
- 立刻给一条整款替代（如 麦香鱼 → 双层深海鳕鱼堡），写清蛋白质与价格差；
- 说明这是「整款换掉」，不是「在原款上加一块」。
- 若阶梯起点不在当前餐单中，明确它只是可查询的整款替代示例，不能假装已经替换当前商品。升级阶梯没有覆盖的商品，只能说「本轮返回的阶梯未提供」，不能断言整个门店或所有渠道都不存在。任何另行建议的组合未经报价，只能标为参考金额，不能声称已验证可点或已报价。

### 6. 券后价（只读试算，不领券）

```bash
curl -s "localhost:8765/api/coupons?meal=lunch"
curl -s -X POST localhost:8765/api/quote_with_coupon -H 'Content-Type: application/json' \
  -d '{"meal":"lunch","signature":"<当前方案的 signature>"}'
```

- 券后价**只引用这两个接口的返回**：`status=ok` 才显示 `coupon.amount` 与 `protein_per_yuan`；`status=none` 就说「暂无可用券」，不要自己算折扣。
- 不调用领券接口、不领券、不核销、不下单；券 id/code 不要复述给用户。
- 结果里必须带「券后价来自门店试算，下单时以 App 为准」；原价与券后价同时给出。
- 换方案、改数量、去料后签名会变，旧的券后价作废，必须用新签名重查。

### 7. 门店与时段

```bash
curl -s -X POST localhost:8765/api/settings -H 'Content-Type: application/json' \
  -d '{"store":{"code":"1440054"},"date":"2026-10-20","meal_times":{"breakfast":"08:15","lunch":"12:15","dinner":"18:30"}}'
```

## 结果怎么读

- `strict_mode: true`、`nutrition_basis: "official_standard"`（`/api/meal` 顶层）→ 全部数字为官方标准版；`is_estimate` 恒为 false。
- `protein_status`：`met` 达标 / `near` 接近达标（比目标少 `protein_gap` g，在 `protein_tolerance` 内）/ `under` 预算内最多。`feasible` 表示「在容差内可行」，`near` 时它是 true，但**不是达标**。
- `saver`：主方案达标时才可能出现，只有一条，`items[]` 是完整组合；转述时用它，不要自己拼商品。
- `frontier.max_protein` → 硬约束下的可达上限；`reach_price` → 只放宽价格的最低方案；`relax_fat` / `relax_kcal` → 只放宽该项需要的数值。`items` 是完整组合，转述时用它，不要只念 `name`（`name` 只是第一个主食）。
- `suggestions[].muscle_warning` → 「增肌时不建议去掉，会减少蛋白质」。
- `suggestions[].protein_risk` → 酱类恒为 false。
- `upgrades[].show_conversion` → 蛋白质增量 ≥ 5 g 才给「每多 1 元多多少 g」。
- `context_warning` 只出现在 `/api/meal` 的返回（页面另从 `/api/bootstrap` 的 `cache.menu_warnings` 读）；非空表示该餐次时间落在两个供餐时段之间（例如 10:30 返回的是早餐菜单），必须原样告诉用户，不要当成正常午餐。`/api/quote`、`/api/recompute` 没有这个字段，不要去找。
- `removable.ok: false` 或 `options` 为空 → 这家店这个 SKU 没返回可去项，直接说明，不要编。

## 标准话术

### 交付前的数值与措辞自检

- 商品金额与预算是两件事。比较「更便宜／更贵／节省」之前，必须比较前后两份方案的实际金额；相同就说「相同」。例如两份方案都是 39 元，不能因预算从 35 变 45 就说「比刚才 39 元的更便宜」。用户只改预算时，不需要主动追加这种比较。
- 去料后的营养只说明「标准版显示值未扣减、实际未知／可能低于显示值」。**不要在这一说明后再追加「只会更低、不会更高、一定更低」等确定性结论。** API 的增肌警示如需展示，标明「接口提示」并原文引用，不能扩写成自行推断。
- 去料回复用三部分即可：实际去除动作；组合试算总价及价格口径；上述标准版营养显示值与实际未知说明。说清即结束，不追加常识推测或未询问的严格目标结论。
- 所有最便宜／最高／无解的判断必须与本次接口的约束口径一致，不能由一份便宜方案外推整个候选集合。不能使用「还便宜／更稳妥」等修饰词替代未经核验的比较。
- 写最终用户回复前，按以上规则逐条自查并更正草稿。

- 加料：「本工具不支持加肉饼/追加配料，只能去料。想吃更多肉可以整款换：<升级阶梯里的一条>。」
- 下单：「本工具只做查询和试算，不能下单或付款；报价是时段试算参考价，不是结算价。」

遇到下单请求时，直接用上述拒绝话术结束本步；**不再附加购物清单、单品金额或营养数字，不调用任何接口，也不转到其他 MCP 或网页替用户完成交易。** 这一步不需要重复求解或报价。

## 限制

- 仅珠海屏湾一路 `1440054` 已验证；换门店需重新刷新菜单与营养匹配。
- 纯牛奶(盒装) 因营养表同名重复无法唯一匹配，不进入计算。
- 可去项取决于 POS 是否返回 `unselectedKey`。
- 报价为时段试算参考价，不是结算价。
- 单次只算一餐，不保存历史。

## 边界

只读查询与试算：不下单、不领券、不兑换积分、不新增地址、不输出 Token。
