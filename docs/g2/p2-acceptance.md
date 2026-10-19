# P2 验收记录：真实 taxonomy + 13 条钢铁指标

日期：2026-10-19（Asia/Shanghai）。范围：I-006 真实行业配置、S-005 PMI 真实版式、
利润表全列扩展，以及 §18.3「≥6 条核心指标」数据门禁。

## 1. 结果摘要

| 项目 | 结果 |
|---|---|
| 真实文档 | 5 份（3 份工业利润 + 1 份 PMI + 1 份工业增加值） |
| 指标序列 | **13 条**（§18.3 要求 ≥6） |
| 观测版本 | **25 条** |
| 覆盖类别 | 利润 ✅ 生产 ✅ 宏观 ✅ 期货 ❌ |
| 冻结快照 | `g2-steel-2026-10-19` |
| taxonomy | `config/industries/steel-g2.yaml`，`synthetic: false` |

## 2. 指标清单

| series_id | 单位 | 期数 | 维度 | 角色 |
|---|---|---|---|---|
| `cn.nbs.industry_c31.revenue_ytd` | 亿元 | 3 | demand | feature |
| `cn.nbs.industry_c31.revenue_ytd_yoy` | % | 3 | demand | feature |
| `cn.nbs.industry_c31.cost_ytd` | 亿元 | 3 | supply | feature |
| `cn.nbs.industry_c31.cost_ytd_yoy` | % | 3 | supply | feature |
| `cn.nbs.industry_c31.profit_ytd` | 亿元 | 3 | profitability | **label** |
| `cn.nbs.industry_c31.profit_ytd_yoy` | % | 3 | profitability | feature |
| `cn.nbs.pmi.headline` | % | 1 | demand | macro_proxy |
| `cn.nbs.pmi.production` | % | 1 | supply | macro_proxy |
| `cn.nbs.pmi.new_orders` | % | 1 | demand | macro_proxy |
| `cn.nbs.pmi.raw_inventory` | % | 1 | inventory | macro_proxy |
| `cn.nbs.pmi.employment` | % | 1 | supply | macro_proxy |
| `cn.nbs.pmi.supplier_delivery` | % | 1 | supply | macro_proxy |
| `cn.nbs.industry.c31` | % | 1 | supply | feature |

实测值（节选）：2025-07 营收 44246.2 亿元 / 成本 41934.0 / 利润 643.6；
2026-09 PMI 50.1、生产 51.7、新订单 50.5；2026-09 行业增加值同比 1.6%。

## 3. §20.5 模式对照（13 序列 × 6 个时点）

```text
as_of                     mode                 covered    cov  sel  skip
2025-08-27T09:30:00+08:00 reconstructed_pit          6    46%    6    19
2025-12-27T09:30:00+08:00 reconstructed_pit          6    46%   12    13
2026-09-28T09:30:00+08:00 reconstructed_pit          6    46%   18     7
2026-09-30T09:30:00+08:00 reconstructed_pit         12    92%   24     1
2026-10-19T09:59:00+08:00 reconstructed_pit         12    92%   24     1
2026-10-19T10:00:00+08:00 reconstructed_pit         13   100%   25     0
（STRICT 全时点 0%，原因见 §5）
```

工业增加值公告发布时间为 10:00，`09:59` 不可见、`10:00` 立即可见——分钟级发布
证据与 as-of 边界严格生效。

## 4. 本轮修复的三处真实版式差异

1. **表头横幅行**（`core/html_tables.header_span`）
   官方 PMI 表首行是整行 `单位：%`（合并单元格展开后每格相同）。原逻辑把它当表头
   存根，导致后续表头识别失败。现在先跳过「整行文本相同」的横幅行，再取首列相同的
   连续表头块。

2. **PMI 版式与 `parse_pmi` 相反**（S-005 从未对真实页面验证）
   真实页面是**行=月份、列=指标**，而 `parse_pmi` 假设行=指标（合成 fixture 形状）。
   新增 `parse_pmi_matrix()`，两个形状并存，调用方显式选择，不自动判别。

3. **财务表不止利润一列**
   `parse_profit` 原来写死 `利润总额`。新增 `metric` 参数（默认不变），使同一份公告
   可产出 营业收入/营业成本/利润总额 × 金额/同比 共 6 条序列；
   `resolve_profit_column` 泛化为 `resolve_table_column`。

另注：IVA 表的产量行标签带单位（`粗钢（万吨）`），与 `parse_iva` 的允许集合
`{粗钢, 钢材, 生铁}` 不匹配而被拒——本轮只用行业增加值行，产品产量待后续适配。

## 5. STRICT 仍是 0%：这是结论，不是缺陷

5 份文档都是**今日抓取的官网现值**，无法举证为当期原件。`value_evidence` 按纪律
停留 C，STRICT（要求 ≥B）一律排除。要启用 STRICT 必须人工核验同期原件后
以 `--contemporary` 重新摄取；本轮**未做**该主张。

## 6. 距 §18.3 的差距

- **期货缺失**：`parse_shfe` 只接受已规整的行数据，没有 SHFE 日行情 HTML/JSON 适配器
  （S-007 仍需真实页面验证）。§18.3 要求「期货与宏观至少各 1 条」，宏观已满足，期货未满足。
- 期数偏少：利润类 3 期、PMI/IVA 各 1 期，不足以做真实 OOS；需按月回填。
- 产品产量（粗钢/钢材/生铁）未接入。
- taxonomy 仅覆盖 C31 一个行业节点。

## 7. 复现

```powershell
industrypulse source permit SRC-NBS-PROFIT --as cn.nbs --database data/g2.duckdb `
  --reason "<许可核验依据>" --actor "<操作人>" --audit data/registry-audit-g2.jsonl
industrypulse init --database data/g2.duckdb --taxonomy config/industries/steel-g2.yaml
industrypulse backfill --database data/g2.duckdb `
  --manifest data/incoming/g2-manifest.json --snapshot-id g2-steel-2026-10-19
industrypulse compare-modes --database data/g2.duckdb --snapshot-id g2-steel-2026-10-19 `
  --series ... --as-of ... --out artifacts/reports/g2-steel-comparison.json
```