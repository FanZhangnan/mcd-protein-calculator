# MCP 集成说明

本项目的菜单、营养、价格数据全部来自麦当劳中国 MCP 服务（`https://mcp.mcd.cn`，streamable HTTP）。不 mock 数据、不硬编码价格。

---

## 1. 用到的工具

| 工具 | 用途 | 本项目的调用点 |
|---|---|---|
| `now-time-info` | 取服务器日期/时区，决定规划日期 | `Service.refresh()` |
| `query-meals` | 按门店 + 餐次时间取菜单 | `refresh()` 三餐各一次 |
| `list-nutrition-foods` | 全量营养表（160 行 / 158 个唯一名称） | `refresh()` 一次 |
| `query-meal-detail` | 套餐 rounds、单品特调项 | `combo_swap()`、别名表构建 |
| `calculate-price` | 单餐报价（带 `reservationDate`） | `Service.quote_meal()` |

`query-nearby-stores` 只在选门店时用，运行时不依赖。

---

## 2. Token 从哪来（不入库）

`src/mcp_client.py::resolve_token()` 按顺序取：

1. 环境变量 `MCD_MCP_TOKEN`（可带或不带 `Bearer ` 前缀，代码会自动补）
2. 本机 `~/.workbuddy/mcp.json` 里 `mcpServers["mcd-mcp"].headers.Authorization`

```bash
export MCD_MCP_TOKEN="<你的 token>"
# 或者依赖已连接的 WorkBuddy 配置，什么都不用设
```

可选覆盖：

| 变量 | 默认 | 说明 |
|---|---|---|
| `MCD_MCP_TOKEN` | 空 | 直接给 token |
| `MCD_MCP_CONFIG` | `~/.workbuddy/mcp.json` | 配置文件路径 |
| `MCD_MCP_SERVER` | `mcd-mcp` | 配置里的 server key |
| `MAI_MAI_DATA` | `mai-mai-planner/data` | 缓存与记录目录 |

**约束**：Token 不打印、不写日志、不进前端、不进 fixture、不进 `mcp-config.example.json`。`mcp_client` 只把 token 放进请求头，任何异常信息都不带它。

---

## 3. 协议细节（踩过的坑）

### 3.1 Authorization 必须带 `Bearer `

配置里如果是裸 token，服务端会返回：

```json
{"code": "400003", "msg": "校验鉴权authToken必填!"}
```

补上 `Bearer ` 前缀后 `initialize` 才 200。代码里已统一用 `_norm_bearer()` 兜住。

### 3.2 到店自取不传 beCode

`beType=1` + `orderType=1` 时 **不要传 `beCode`**，传了会报错。

### 3.3 `calculate-price` 必须带 `reservationDate`

不带时只按**当前时刻**在售判断。凌晨给明天的晚餐报价会全量返回：

```
商品已售罄或不可售，换一个商品或检查orderType/beType
```

带上 `reservationDate: "2026-10-20 18:30"` 后同一商品正常返回 `price: 2400`。

这也是为什么本项目每餐单独报价，且报价里会回传 `reservation_date`。

### 3.4 金额单位是分

`data.price` / `data.originalPrice` / `data.discount` 都是**分**。展示一律 ÷100。

### 3.5 特调（`modification`）传参

对含 `unselectedKey` 的特调组，**组内所有项都要传**：选中用 `selectedKey`，未选中的用 `unselectedKey`，否则整组可能被忽略。

去酱场景示例（麦辣鸡腿汉堡 `1440`，麦香鸡酱 `100136`）：

```json
{"productCode": "1440", "quantity": 1,
 "modification": {"values": [
   {"code": "100136", "key": "0-0", "quantity": 1},
   {"code": "120512", "key": "0-1", "quantity": 1}
 ]}}
```

去酱本身 `price: 0`，所以价格不变是**正确**结果，不是接口故障。

### 3.6 套餐换配走 `roundList`

```json
{"productCode": "9900005462", "quantity": 1,
 "roundList": [{"round": "2", "comboItemList": [{"code": "4820", "quantity": 1}]}]}
```

实测：默认 3450 分 → 换大薯条 3700 分（+250，与 `diffPrice: +¥2.5` 一致）。只传目标 round 即可，不必全量回传。

### 3.7 菜单按餐次时间变化

同一门店，`reservationDate` 不同返回不同 SKU 集：

- `08:15` → 超值早餐 / 麦满分系列 / 粥王系列 / 早安营养卷 …
- `12:15` / `18:30` → 鸡肉汉堡卷 / 巨无霸 / 安格斯 / 蘸料炸鸡 …

所以早餐池与正餐池必须分开取，不能复用。

---

## 4. 数据落盘

```
data/cache/<yyyy-MM-dd>.json   当日三时段菜单 + 营养表 + 抓取时间
data/fixtures/*.json           真实响应快照（离线测试用，无 Token）
data/aliases.json              code → 权威名称 + 证据
data/state.json                目标/门店/时间/情景
data/intake.json               已吃记录（含营养与价格快照）
```

缓存带 `fetched_at`，界面顶部显示数据来源（`live` / `cache` / `offline`）与时间，不会把旧数据当实时结果。

---

## 5. 失败处理

| 情况 | 表现 |
|---|---|
| 无 Token | `McpError(stage="auth")`，界面提示，不降级造假 |
| 网络/超时 | `stage="transport"` |
| 业务失败（如商品不可售） | 返回 `{status:"failed", message: 原始 message}`，金额为 `null` |
| 报价失败 | 该餐显示"报价失败：…"，其余餐不受影响，方案仍可看 |
| 缺营养 | SKU 列入"数据缺失"，不进入求解，合计标"缺项" |

---

## 6. 明确不做的事

- 不下单（`create-order` 从未调用）
- 不取消订单、不领券、不用积分、不抽奖
- 不新增配送地址
- 不请求外送场景
- 不在前端或示例配置里放任何真实 Token
