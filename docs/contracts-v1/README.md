# contracts-v1 — G0 契约冻结发布说明

- 发布版本：`CONTRACTS_VERSION = 1.0.0`（`src/industrypulse/contracts/common.py`）
- 冻结任务：C-002..C-008、C-009、T-001..T-003（任务书 §17.2/§18.1）
- 锁定方式：git 标签 `contracts-v1` + `contracts/MANIFEST.json`（全部生成物 sha256 清单）
- 变更流程：任何字段/语义变更 → 递增 `CONTRACTS_VERSION` → 重跑
  `python scripts/export_contracts.py` → 单独的 contracts PR 评审 → 各 lane 联动迁移。
  **模块开发者不得自行调整共享字段**（§3.3）。

## 1. 契约清单（Python 真值 → 生成物）

| 模块 | 主要合同 | 任务 | 语义锚点 |
|---|---|---|---|
| `contracts.common` | ContractBaseModel（extra=forbid, frozen）、时区/标识符标量 | C-002 | §6.1、§7.1 |
| `contracts.sources` | SourceDescriptor、SeriesSpec、ReleasePolicy、许可/状态机枚举 | C-002 | §4.3、§7.5 |
| `contracts.observation` | ObservationVintage、EvidenceCandidate、RawArtifact、ResearchQuery | C-003 | §5.4、§6.2、§7.2 |
| `contracts.context` | ResearchMode、AnalysisContext、DataQuality、MODE_EVIDENCE_GATES | C-004 | §6.3、§7.4 |
| `contracts.pit` | select_vintages_as_of、effective_available_at、SkipReason | C-004 | §6.4、§7.3 |
| `contracts.domain` | IndustryNode、IndicatorSpec、AssetMaster、MarketBar | C-005 | §7.1、§8.1、§13.1 |
| `contracts.model` | FeatureFrame、TargetFrame、Prediction、Repository/Model 协议 | C-006 | §6.6、§10、§11.6 |
| `contracts.research` | ExperimentSpec、BacktestEvent、JobStatus、SplitSpec | C-007 | §12.4、§13.3 |
| `contracts.http` | ErrorCode、ApiResponse、ROUTES、示例工厂 | C-008 | §14 |

JSON Schema 与 OpenAPI 样例见 `contracts/`（生成物，勿手改）。

## 2. 模块路径所有权表（并行分工的文件边界）

| Lane | 唯一自有路径 | 输入合同 | 首个交付（G1） |
|---|---|---|---|
| S 数据源 | `src/industrypulse/datahub/` | SourceDescriptor, RawArtifact | S-001..S-009 |
| A AI 证据 | `src/industrypulse/ai_research/` | ResearchQuery, EvidenceCandidate | A-001..A-008 |
| D 仓储/PIT | `src/industrypulse/repositories/` | Observation, pit 语义 | D-001..D-010 |
| I 行业知识 | `src/industrypulse/taxonomy/`、`config/industries/` | IndustryNode, IndicatorSpec | I-001..I-005 |
| E 景气引擎 | `src/industrypulse/economy/` | ObservationRepository | E-001..E-006 |
| M 预测模型 | `src/industrypulse/features/`、`src/industrypulse/forecast/` | FeatureFrame, TargetFrame | M-001..M-009 |
| R 研究验证 | `src/industrypulse/research/` | ExperimentSpec, Prediction | R-001..R-008 |
| V 投资研究 | `src/industrypulse/investment/` | ForecastSnapshot, MarketBar | V-001..V-003 |
| U 前端 | `apps/dashboard/` | OpenAPI + 示例载荷 | U-001..U-006 |
| O 运维 | `src/industrypulse/jobs/`、`src/industrypulse/core/`、`src/industrypulse/services/` | JobStatus | O-001..O-005 |
| T 测试 | `tests/`（按目录分工：contracts/pit 归 G0，unit 按模块） | 全部 | T-004..T-006 |
| C/API | `src/industrypulse/api/`、`contracts/` | 全部 | G1 编排层 |

共享区（改动须全 lane 评审）：`pyproject.toml`、`.github/workflows/`、
`src/industrypulse/contracts/`、`tests/fixtures/`、`tests/conftest.py`。

## 3. Mock 端点清单（U 线无后端可跑 UI 的依据）

路由全集 = `industrypulse.contracts.ROUTES`（15 条，§14.2 一致）。示例载荷：

| 路由 | mock 载荷 |
|---|---|
| `GET /v1/industries/{id}/state` | `contracts/openapi/examples/industry_state.json` |
| 任意 4xx/5xx | `contracts/openapi/examples/error_payload.json` |
| 其余 GET | 以 `ApiResponse` 外壳 + 各 result_kind 的空骨架生成（U-002 落地） |

`POST /v1/research/discover` 默认关闭，仅单机配置合法检索服务后启用（§14.2）。

## 4. 合成 Fixture 索引（tests/fixtures/g0/，C-009 冻结场景）

| 文件 | 场景 |
|---|---|
| `steel_profit_vintages.json` | 三版本修订共存、未来发布、B/C/D 证据、跨年 YTD、缺失行 |
| `market_calendar.json` | 合成交易日历（国庆/元旦/春节假日边界） |
| `etf_universe.json` | 晚上市、退市、未上市、指数代理（PIT-008/V-009） |
| `market_bars.json` | 节前节后日线、上市首日 |
| `corporate_actions.json` | 现金分红除息（INVEST-002） |
| `evidence_candidates.json` | verified / FETCH_FAILED / 冲突对 NEEDS_REVIEW（PIT-010/A-008） |
| `steel_taxonomy.json` | C31 节点、上下游关系、LABEL/MACRO_PROXY 指标 |

这些 fixture 是全部 lane 的 mock 数据源；新增场景须走共享区评审。

## 5. 冻结的 PIT 语义要点（D 线 SQL 实现必须逐条对齐）

1. 三模式证据门槛：`MODE_EVIDENCE_GATES`（STRICT: 双 ≥B；RECON: 双 ≥C；EXPLORATORY: 无门槛）；
2. B 级发布证据（只知日期）→ 次一交易日 09:30 Asia/Shanghai 后可用；
3. RECONSTRUCTED 对 C 级发布证据叠加保守滞后（默认 5 自然日，按系列 p99 配置）；
4. 同周期多版本 tie-break：`available_at DESC, vintage_id DESC`（§7.3）；
5. `basis=retrieval_time` 的行只允许 EXPLORATORY；
6. 缺失 = `value=None + missing_reason`，禁止 0 填充；
7. 负例守护：`tests/pit/`（PIT-001..PIT-006、PIT-010 + 属性测试）。

## 6. 已知开放点（不阻塞 G1 并行启动）

- `ApiResponse.result` 的各 result_kind 强类型（EconomyReport 等）由对应 lane 在 G1
  提 PR 收紧，G0 冻结的是外壳与错误协议；
- `hypothesis` 属性测试目前覆盖 pit 选择语义，G1 起按 lane 扩展；
- 正式 ADR（Python 3.13 决策）待补 `docs/adr/`。
