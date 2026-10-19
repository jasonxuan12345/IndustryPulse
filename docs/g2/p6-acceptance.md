# P6 验收记录：ETF 复权口径验证 + STRICT 证据链落地

日期：2026-10-19。承接 `probe-report.md` 的两个结论，落实第一批实现。

## 1. ETF 复权口径：已定论（不复权）

用两套独立数据交叉判定：

| 对比 | 最大绝对差（收盘价，641 个重叠交易日） |
|---|---|
| 新浪 vs 腾讯 **不复权** | **0.000000** |
| 新浪 vs 腾讯 **前复权(qfq)** | 0.120000 |

**结论：新浪 K 线是不复权原始价**。这对本项目是最有利的结果——
`investment/market.py::total_return` 本就要求 `adj_method="none"` 并用现金分红加回，
口径完全对齐。

抽样：

```text
2024-02-26  sina=1.1690  tx_raw=1.1690  tx_qfq=1.0490
2026-10-19  sina=1.1130  tx_raw=1.1130  tx_qfq=1.1100
```

## 2. 分红：能确认存在，但拿不到官方逐笔记录

用「raw/qfq 比值跳变」反推（阈值 0.003），得到两次清晰事件：

| 除息日 | 隐含每份分红 | 前收 |
|---|---|---|
| 2024-12-24 | ≈0.0617 元 | 1.212 |
| 2025-12-24 | ≈0.0607 元 | 1.430 |

与新浪基金页显示的「累计分红 0.225 元」量级一致（更早年份另有分红）。

**但官方逐笔来源全部不可用**：

| 来源 | 结果 |
|---|---|
| `fundf10.eastmoney.com`（东财分红页） | DNS 解析失败 |
| 新浪分红接口 `FundPageInfoService.tabjjfh` | `Service not found` |
| 新浪 `/fund/quotes/515210/fh.shtml` | 404 |
| 巨潮 `topSearch` 取 orgId | POST 返回空数组 / GET 500 |

→ **V-006 必须把分红来源做成可插拔**：`none`（默认，回测报告价格收益并显式标注）
/ `derived`（用 qfq-raw 比值推导，C 级）/ `file`（人工导入官方公告）。
不得默认静默忽略分红。

## 3. STRICT：裸开关已移除，换成证据链

### 3.1 变更

| 变更前 | 变更后 |
|---|---|
| `--contemporary` 布尔开关（人工口头保证） | `--as-published auto` 或 `--as-published capture:<ISO8601>` |
| `IngestService(contemporary=True)` | `IngestService(as_published=AsPublishedClaim(...))` |
| 无审计痕迹 | 判定结果写入 `vintage.notes`，并作为 `report.claim` 返回 |

### 3.2 判定规则（`services/as_published.py`）

```text
无证据                        -> C
无 publication_at             -> C（无法计算窗口）
捕获时点 < 发布时间            -> C（证据自相矛盾）
捕获时点 - 发布时间 > 48h      -> C（修订窗口已开）
0 <= 窗口 <= 48h              -> 允许升格（A）
```

### 3.3 实测

历史回填时用 `--as-published auto`：

```json
{
  "created": 0,
  "value_evidence": "C",
  "claim": "as_published rejected: 距发布 10042.4h 超出 48h 窗口"
}
```

非法方法（如 `wayback:`）直接报错：`未支持的证据方法：'wayback'（可选 ('capture',)）`。

**这正是预期行为**：历史 42 份文档抓取于发布后约 10000 小时，无法主张同期原件，
必须停在 C。STRICT 只能从今往后累积。

## 4. 新增/变更文件

| 文件 | 说明 |
|---|---|
| `services/as_published.py` | 证据声明、解析、48h 窗口判定 |
| `services/ingest.py` | `contemporary` -> `as_published`；报告新增 `claim` 字段 |
| `services/monthly_backfill.py` | 透传证据声明 |
| `cli.py` | 三个摄取命令改用 `--as-published` |
| `tests/unit/services/test_as_published.py` | 边界（48h 整点通过、48h+1min 拒绝）等 10 项 |

## 5. 下一步

1. V 线实现时按 §2 的可插拔分红设计落 `CorporateAction` 装载。
2. 前向捕获：下一次 NBS 公告发布后 48h 内用 `--as-published auto` 摄取，
   即可产生第一批 STRICT 可用样本。
3. 多年回填继续走 RECONSTRUCTED，报告口径必须写明。