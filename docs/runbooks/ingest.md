# 真源摄取 Runbook（P0 工程入口）

适用版本：`0.1.0` / contracts-v1，`industrypulse` CLI（IP-009 落地后）。
本手册只描述**怎么把一份真实官方文档安全地变成可 PIT 查询的 Silver 观测**，
不替代 §20 的真实源验收（许可、跨月样本、首发历史覆盖仍须逐项人工核验）。

## 0. 前置认知

系统有三道不允许绕过的闸门，它们会"正确地拒绝"你：

| 闸门 | 触发条件 | 表现 |
|---|---|---|
| 许可 | 源为 `pending_review` / `license_required` / `denied` | `PublicationError: source license does not permit archival` |
| 出处 | 观测没有 Bronze 原件祖先，或原件哈希不符 | `PublicationError: observation has no Bronze ancestor` / `missing or corrupted Bronze artifact` |
| 单位 | 解析单位与 `SeriesSpec.unit` 不一致 | `IngestError: 单位不一致…禁止静默换算` |

拒绝是预期行为，不要为了"跑通"去放宽这些检查。

## 1. 命令总览

```text
industrypulse doctor                         环境、依赖、契约版本自检
industrypulse source list                    列出来源目录与许可状态
industrypulse source permit <目录ID> --as <合同ID> ...
                                             完成许可核验并登记来源（写审计日志）
industrypulse init --database <db>           登记 taxonomy / series
industrypulse calendar validate <文件>       校验交易日历并记录指纹
industrypulse ingest ...                     摄取一份文档 -> Bronze -> Silver
```

## 2. 标准流程

### 2.1 自检

```powershell
.venv/Scripts/industrypulse.exe doctor
```

### 2.2 许可核验（必须先做，且必须真的做过）

`source_catalog.csv` 里的 `verification` 列只说明"页面可访问"，**不等于获得采集许可**。
人工确认 robots.txt / 转载条款后，才能给出 `legal_status` 并写明依据：

```powershell
.venv/Scripts/industrypulse.exe source permit SRC-NBS-PROFIT `
  --as cn.nbs `
  --database data/industrypulse.duckdb `
  --reason "统计局官网公开新闻稿，允许非商业引用，2015-2025 公告页均可直接访问" `
  --actor "<你的名字>" `
  --audit data/registry-audit.jsonl
```

- 目录 ID（`SRC-NBS-PROFIT`）与合同 `source_id`（`cn.nbs`）是两个命名空间，
  `--as` 是它们的显式桥接，映射关系会写入审计日志。
- `--legal-status` 可选 `public_official`（默认）/ `manual_export_allowed` 等。
- 同一 `source_id` 一旦登记即不可改写；如需变更状态，重新走审查流程。

### 2.3 登记 taxonomy

```powershell
.venv/Scripts/industrypulse.exe init --database data/industrypulse.duckdb
```

未登记序列会在摄取时报 `未登记 SeriesSpec`。`config/industries/steel.yaml`
当前标记 `synthetic: true`，真实接入前须替换为已核验的行业/序列配置。

### 2.4 校验交易日历（STRICT PIT 的硬前置）

```powershell
.venv/Scripts/industrypulse.exe calendar validate data/sse-calendar.json `
  --exchange SSE --out data/calendar-normalized.json
```

- 只接受 JSON（`trading_days` 或 `days`）或带 `date` 列的 CSV。
- 拒绝：空日历、重复日期、周六日、间隔超过 `--max-gap-days`（默认 14 天）的断层。
- 输出含文件 sha256 与版本号，用于证明"这次回测用的是哪版日历"。
- **没有真实日历会导致 B 级规则（次一交易日 09:30）静默退化为 +1 自然日**，这是必须避免的。

### 2.5 摄取

先把官方文档保存为本地 UTF-8 HTML，再摄取：

```powershell
.venv/Scripts/industrypulse.exe ingest `
  --database data/industrypulse.duckdb `
  --source-id cn.nbs `
  --file incoming/2025-08-27-profit.html `
  --period 2025-07 `
  --source-url "https://www.stats.gov.cn/sj/zxfb/202508/t20250827_1960884.html" `
  --parser profit `
  --params-file incoming/params-profit.json
```

`params-profit.json`：

```json
{ "row": "黑色金属冶炼和压延加工业", "column": "利润总额", "unit": "亿元" }
```

> Windows PowerShell 5.1 会剥掉 `--params` 中的引号，**建议一律用 `--params-file`**。

可用 `--parser`：`profit` / `iva` / `pmi` / `tsf`（参数键分别见
`datahub/adapters.py` 中各 `parse_*` 函数签名）。

## 3. 证据分级：`--contemporary` 是本系统最重要的一个开关

摄取默认给出：

- `release_evidence`：由文档自带的发布时间精度推出（秒/分 → A，仅日期 → B，月份/未知 → C）
- `value_evidence`：**默认 C**，含义是"今天抓到的现行修订值"

只有当你**人工核验过这份文件是当期原件**（而不是官网当前展示的最新修订版）时，
才能加 `--contemporary`：

```powershell
... --contemporary
```

这不是可选优化，而是结论强度声明。C 级数值在 STRICT PIT 下会被
`INSUFFICIENT_VALUE_EVIDENCE` 排除——这是设计意图，不是 bug。
若只想演示链路，请用 `--mode reconstructed_pit` 或 `exploratory` 查询。

## 4. 幂等与修订

- 同一原件 + 同一摄取选项重复执行 → `created: 0, reused: 1`，快照 ID 不变。
- 内容变化（新公告/修订值）→ 新 `vintage_id`，旧行永不改写。
- 改变证据主张（加 `--contemporary`）→ 新快照 ID + 新 `vintage_id`，
  因为"重新定级"本身是需要留痕的事件。
- 需要固定快照名（例如冻结实验用）时显式传 `--snapshot-id`。

## 5. 常见错误与处置

| 报错 | 含义 | 处置 |
|---|---|---|
| `未登记 SeriesSpec` | 序列未注册 | 先 `source permit` 再 `init` |
| `source license does not permit archival` | 许可未核验 | 走 §2.2 |
| `unit/scale/currency mismatch` | 解析单位与规格不符 | 修正 `--params` 的 `unit`，**不要**改规格去迁就解析 |
| `missing or corrupted Bronze artifact` | 原件被移动/篡改 | 重新摄取；Bronze 只增不改 |
| `immutable snapshot_id conflict` | 同名快照内容不同 | 换 `--snapshot-id` 或让默认内容寻址生效 |
| `--params 不是合法 JSON` | PowerShell 引号被剥 | 改用 `--params-file` |
| `日历…间隔超过 N 天` | 日历缺年份/被截断 | 核对官方年度休市安排文件 |

## 6. 已知限制（P0 范围内不做）

- 只有 `profit`/`iva`/`pmi`/`tsf` 四个 HTML 解析器接入 CLI；
  SHFE 行情与库存周报需要 rows 输入，尚未接 CLI。
- 无网络抓取命令：P0 要求先人工保存原件，再由 `ingest` 归档。
  自动抓取入口是 G2 `S-002` 的后续集成。
- `vintage_id` 分配在"读取已有版本"与"发布"两个事务之间完成，
  并发摄取同一序列-所属期可能撞键并报 `immutable vintage conflict`。
  单机单写场景可接受，多进程并发需在 G2 `D-011` 统一查询 SDK 时收敛。
- 尚未实现修订差异报告（`D-012`）与备份恢复（`D-013`）。