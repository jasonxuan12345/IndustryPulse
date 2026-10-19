# P3 验收记录：上期所期货合约日行情 + 交易日历陷阱

日期：2026-10-19（Asia/Shanghai）。范围：补齐 §18.3「期货至少 1 条」的剩余缺口。

## 1. 结果

| 项目 | 结果 |
|---|---|
| 文档 | 7 份（3 利润 + 1 PMI + 1 工业增加值 + 1 SHFE 日行情，覆盖 2 合约） |
| 指标序列 | **15 条**（新增 RB2701/HC2701 结算价） |
| 观测版本 | **27 条** |
| 类别覆盖 | 利润 ✅ 生产 ✅ 宏观 ✅ **期货 ✅** |
| 冻结快照 | `g2-steel-2026-10-19b` |
| taxonomy | `steel-g2-2026-10-19b`（`synthetic: false`） |

新增两条期货序列：

| series_id | 值 | 发布时点 | 证据 |
|---|---|---|---|
| `cn.shfe.rb2701.settlement` | 3080 元/吨 | 2026-10-19 15:56:16 | A/C |
| `cn.shfe.hc2701.settlement` | 3250 元/吨 | 2026-10-19 15:56:16 | A/C |

## 2. 数据入口

上期所站点已改为 JS 应用，旧接口 `/data/dailydata/kx/*.dat` 返回 404。
从 `/images/api.js` 中定位到现行端点：

```text
https://www.shfe.com.cn/data/tradedata/future/dailydata/kx{YYYYMMDD}.dat
```

返回 JSON，字段含 `o_curinstrument[]`（`PRODUCTID`/`DELIVERYMONTH`/`SETTLEMENTPRICE`/
`VOLUME`/`OPENINTEREST`）与 `update_date`（如 `20261019 15:56:16`，即发布时点）。

## 3. 关键设计：禁止主力拼接

`parse_shfe_daily` 只接受调用方**显式指定**的合约（`--params '{"contract":"RB2701"}'`），
不做"按持仓量自动选主力"——主力切换会造成合约拼接式未来函数（S-007 明令禁止）。
序列身份按合约划分（`cn.shfe.rb2701.settlement`），而不是跨合约的连续价格。

同时修正了一处语义：日频数据的 `observation_start/end` 必须是**真实交易日**，
不能按月近似。`ParsedObservation` 增加可选边界字段，`IngestService` 优先使用；
否则 2026-10-19 的结算价会被记成"2026 年 10 月整月"。

## 4. 模式对照（15 序列，含秒级边界）

```text
as_of                     mode                 covered    cov  sel  skip
2025-08-27T09:30:00+08:00 reconstructed_pit          6    40%    6    21
2025-12-27T09:30:00+08:00 reconstructed_pit          6    40%   12    15
2026-09-28T09:30:00+08:00 reconstructed_pit          6    40%   18     9
2026-09-30T09:30:00+08:00 reconstructed_pit         12    80%   24     3
2026-10-19T10:00:00+08:00 reconstructed_pit         13    87%   25     2
2026-10-19T15:56:15+08:00 reconstructed_pit         13    87%   25     2
2026-10-19T15:56:16+08:00 reconstructed_pit         15   100%   27     0
（STRICT 全时点 0%）
```

期货结算价公告时点为 `15:56:16`：`15:56:15` 不可见、`15:56:16` 立即可见——
秒级发布证据与 as-of 边界生效。

## 5. 交易日历陷阱（重要负面发现）

上期所站点提供 `/data/calendar/calendar_2026.dat`，形似交易日历，实测**不可用**：

- 全年仅 134 条，占 2026 年 261 个工作日的 **51.3%**；
- 缺失 2026-10-20/21/22、2026-10-13/14 等**正常交易日**；
- 条目携带 `delivery`、`tradeLastDate`、保证金等字段——它是**交割/特殊事件日历**。

若把它当作 `trading_days` 传给 PIT 选择，B 级发布证据的"次一交易日 09:30"会
静默算错（跳过真实交易日）。因此本轮**没有**接入该文件，`core/calendar.py`
的装载器也**没有**为这种形状开放解析入口。

→ 真实交易日历仍需另找权威来源（或由交易所全部日行情文件反推并交叉校验）。
这是目前唯一已知的、会影响 STRICT 正确性的未决项。

## 6. 距 §18.3 的差距

§18.3 四类数据要求（利润/生产/宏观/期货）**已全部满足**。剩余差距不在数据类别，而在数量与深度：

- 利润类 3 期、PMI/工业增加值/期货各 1 期——不足以做真实 OOS；
- 期货仅 2 个合约、1 个交易日，未做合约生命周期管理；
- 产品产量（粗钢/钢材/生铁）仍未接入；
- 真实交易日历未落地（见 §5）。

## 7. 复现

```powershell
industrypulse source permit SRC-SHFE-RB --as cn.shfe --database data/g2.duckdb `
  --reason "<许可核验依据>" --actor "<操作人>" --audit data/registry-audit-g2.jsonl
industrypulse init --database data/g2.duckdb --taxonomy config/industries/steel-g2.yaml
industrypulse backfill --database data/g2.duckdb `
  --manifest data/incoming/g2-full-manifest.json --snapshot-id g2-steel-2026-10-19b
industrypulse compare-modes --database data/g2.duckdb --snapshot-id g2-steel-2026-10-19b ...
```

注：已有快照不可追加。新增数据必须建新快照（本轮 `...-10-19` -> `...-10-19b`），
这正是"冻结信息集"的应有语义。