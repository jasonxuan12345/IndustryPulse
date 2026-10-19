# P1 验收记录：国家统计局工业利润三份真实公告

日期：2026-10-19（Asia/Shanghai）。执行人：P1 acceptance run。
范围：任务书 §20.1 第 1 条——准确定位"黑色金属冶炼和压延加工业"，读出利润总额/同比，
保存同期发布时间与行列定位；§20.5——STRICT 与 RECONSTRUCTED 的 as-of 对照表。

## 1. 数据获取与许可核验

| 项目 | 结论 |
|---|---|
| 目标站点 | `https://www.stats.gov.cn/sj/zxfb/`（国家统计局 新闻发布） |
| robots.txt | 返回 **404**（站点未发布爬取限制声明） |
| 访问方式 | 公开新闻发布页，无登录、无付费墙、无验证码 |
| 采集量 | 3 份公告页，间隔 ≥1s，单次会话 |
| 许可判定 | `PUBLIC_OFFICIAL`，依据写入 `data/registry-audit.jsonl` |
| 说明 | 页面可访问 ≠ 已完成站点条款审查。批量/长期采集须另行复核转载条款 |

三份公告（§20.1 指定的试验样本）：

| 所属期 | 值（利润总额，亿元） | 同比（%） | 官方发布时间 | URL |
|---|---|---|---|---|
| 2025-07 | 643.6 | 5175.4 | 2025-08-27 09:30 | `.../202508/t20250827_1960884.html` |
| 2025-11 | 1115.0 | 1752.2 | 2025-12-27 09:30 | `.../202512/t20251227_1962158.html` |
| 2026-08 | 294.3 | -62.4 | 2026-09-28 09:30 | `.../202609/t20260928_1965425.html` |

公告标题与总量口径分别为"2025年1—7月利润下降1.7%""2025年1—11月增长0.1%"
"2026年1—8月增长15.7%"。**钢铁行业同比与全国总量方向相反且量级极大，属真实数据**
（2024 年钢铁利润基数极低），不得因"看起来不合理"裁剪或平滑。

## 2. 执行记录

```powershell
industrypulse source permit SRC-NBS-PROFIT --as cn.nbs --database data/industrypulse.duckdb `
  --reason "国家统计局官网公开新闻发布页；robots.txt 返回404无爬取限制声明；..." `
  --actor "p1-acceptance" --audit data/registry-audit.jsonl
industrypulse source permit SRC-SHFE-RB --as cn.shfe --database data/industrypulse.duckdb `
  --reason "上期所官网公开行情页；仅登记占位，本轮不使用其数据" --actor "p1-acceptance"
industrypulse init --database data/industrypulse.duckdb

# 逐份摄取（保留各自快照）
industrypulse ingest --parser profit --params-file data/incoming/params-2025-07.json ...

# 批量回填为同一个冻结快照（O-006）
industrypulse backfill --database data/industrypulse.duckdb `
  --manifest data/incoming/backfill-manifest.json --snapshot-id g2-profit-2026-10-19

# §20.5 模式对照
industrypulse compare-modes --database data/industrypulse.duckdb `
  --snapshot-id g2-profit-2026-10-19 --series cn.nbs.industry_c31.profit_ytd `
  --as-of ... --out artifacts/reports/g2-pit-comparison.json
```

`backfill` 返回 `created: 0, reused: 3`——三份文档此前已各自入库，批量发布复用了
既有版本并按内容寻址建立新快照，幂等语义符合设计。

## 3. §20.5 模式对照结果

```text
snapshot=g2-profit-2026-10-19

as_of                     mode                 covered    cov  sel  skip
------------------------------------------------------------------------
2025-08-27T09:29:00+08:00 strict_pit                 0     0%    0     3   <-- 覆盖不足
2025-08-27T09:29:00+08:00 reconstructed_pit          0     0%    0     3   <-- 覆盖不足
2025-08-27T09:30:00+08:00 strict_pit                 0     0%    0     3   <-- 覆盖不足
2025-08-27T09:30:00+08:00 reconstructed_pit          1   100%    1     2
2025-12-27T09:30:00+08:00 reconstructed_pit          1   100%    2     1
2026-09-28T09:30:00+08:00 reconstructed_pit          1   100%    3     0
```

行级原因（节选）：

- `2025-08-27T09:29` 两种模式均为 `future_release`——发布前一分钟不可见（PIT-001）。
- `2025-08-27T09:30` RECONSTRUCTED 选中 `v1 value=643.6 [A/C]`，STRICT 为
  `insufficient_value_evidence`。
- 随公告陆续发布，RECONSTRUCTED 可见期数 1 → 2 → 3。

### 结论：STRICT 覆盖 0% 是正确结果，不是缺陷

三份 HTML 是**今天抓取的官网现值**，无法证明其为当期原件（官方可能已修订）。
因此 `value_evidence` 按默认纪律停留在 **C**，STRICT（要求 ≥B）一律排除。
要让 STRICT 可用，必须人工核验同期原件后以 `--contemporary` 重新摄取——
本轮**没有**这样做，因为无法举证，也不应为了"让严格模式看起来有数据"而升格证据。

## 4. 本轮发现并修复的两个真实缺陷

1. **表头区判定错误**（`core/html_tables.py`）
   原实现把"目标行之上的所有行"都当表头，真实公告页有 29 行数据在表头之上，
   导致表头标签集合被数据值污染，精确列定位失败（`found 0`）。
   改为只认"从第 0 行起首列仍等于表头存根标签的连续块"。
2. **全角/半角括号差异**
   2025 年页面用 `(亿元)`，2026 年页面用 `（亿元）`，精确匹配会因排版差异失败。
   新增 `datahub.adapters.resolve_profit_column()`：只做确定性结构匹配
   （表头首格 = `利润总额`、存在 `金额`/`同比增长` 子格、单位可归一化），
   匹配不唯一即拒绝；返回的标签仍会由 `locate` 二次精确校验。

两者都只在真实页面上才会暴露——合成 fixture 无法覆盖。

## 5. 产出物

| 路径 | 内容 |
|---|---|
| `data/incoming/*.html` | 三份公告原件（含 sha256，随 Bronze 归档；`data/` 不入 Git） |
| `data/industrypulse.duckdb` | Bronze/Silver 仓储，快照 `g2-profit-2026-10-19` |
| `data/registry-audit.jsonl` | 许可变更审计（actor/reason/时间） |
| `artifacts/reports/g2-pit-comparison.json` | 对照表机器可读版 |

## 6. 明确未完成

- **同比序列未入库**：`cn.nbs.industry_c31.profit_ytd_yoy` 未在 `steel.yaml` 注册，
  解析已验证（5175.4 / 1752.2 / -62.4）但未发布；需在 G2 I-006 一并登记。
- **taxonomy 仍是合成注册表**（`synthetic: true`、`steel-g1-synthetic-v1`），
  真实接入需替换为已核验的行业/序列配置。
- 仅 1 条指标序列、3 个所属期，未达 §18.3「≥6 条核心指标」门槛。
- 未采集 PMI / 工业增加值 / 社融 / 上期所数据。
- STRICT 模式因缺乏同期原件证据而不可用（见 §3 结论）。