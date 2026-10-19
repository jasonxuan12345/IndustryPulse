# STATUS — IndustryPulse

最后更新：2026-10-13（Asia/Shanghai）

## 当前阶段

**G1 十路离线核心闭环已交付**；G0 冻结合同保持不变（标签 `contracts-v1`）。
S/A/D/I/E/M/R/V/U/O 按独占路径实现，并补 T-004..006 和合成跨 lane 集成。
本轮为工程/合成样本验收；真源质量、真实 ETF 主表和预测/投资有效性仍待
各自门禁。后续按 G2 逐源审核接入，再跑真实月末快照和研究。

## P2 真实 taxonomy 与 13 条钢铁指标（2026-10-19）

**§18.3「≥6 条核心指标」数据门禁已达成**：13 条真实序列、25 个观测版本，冻结快照
`g2-steel-2026-10-19`。类别覆盖 利润 ✅ 生产 ✅ 宏观 ✅，**期货 ❌**。

- 新增 `config/industries/steel-g2.yaml`（`synthetic: false`）：C31 节点 + 13 序列
  + 13 指标，全部来自已核验的官方公告字段。
- 5 份文档：3 份工业利润（营收/成本/利润 × 金额/同比 = 6 序列 × 3 期）、
  1 份 PMI（6 分量 × 1 期）、1 份工业增加值（行业当月同比）。
- §20.5 对照：RECONSTRUCTED 覆盖率 46% → 92% → 100%；IVA 公告 10:00 发布，
  `09:59` 不可见、`10:00` 可见，分钟级证据与 as-of 边界严格生效。
- STRICT 仍全时点 0%：5 份文档均为今日抓取的官网现值，未主张同期原件，
  `value_evidence` 保持 C。这是结论，不是缺陷。

本轮修复三处只在真实页面暴露的版式差异：表头整行横幅（`header_span`）、
PMI 真实版式为「行=月份/列=指标」与 `parse_pmi` 假设相反（新增 `parse_pmi_matrix`）、
财务表不止利润一列（`parse_profit` 增加 `metric`，`resolve_table_column` 泛化）。

```text
pytest                         376 passed（比 P1 新增8项）
ruff check / format --check    OK
mypy src                      OK（70 文件，strict）
contracts export --check       OK（共享合同未改）
```

验收记录见 `docs/g2/p2-acceptance.md`。**仍未满足 §18.3**：期货（SHFE）无 HTML/JSON
适配器；期数偏少（利润 3 期、PMI/IVA 各 1 期）；产品产量未接入。

## P1 真源垂直切片（2026-10-19）

**G2 第一条真实数据链已打通**：国家统计局三份工业利润公告（2025-07 / 2025-11 /
2026-08）经许可核验、原文归档、解析入库，形成冻结快照 `g2-profit-2026-10-19`。

- 解析结果：643.6 / 1115.0 / 294.3 亿元；官方发布时间 09:30 精确到分钟，发布证据 A。
- 证据纪律：三份均为今日抓取的官网现值，无法举证为同期原件，`value_evidence` 停留 C。
- §20.5 对照：RECONSTRUCTED 随公告发布可见期数 1→2→3；STRICT 全时点排除
  （`insufficient_value_evidence`）。**STRICT 覆盖 0% 是正确结论，不是缺陷**。
- 新增 `backfill`（O-006 批量回填，多文档 -> 单冻结快照）与
  `compare-modes`（§20.5 三模式对照，含行级原因）。

本轮修复两个只在真实页面暴露的缺陷：
`core/html_tables.py` 表头区判定（原把数据行并入表头，29 行污染导致精确列定位失败）、
以及官方页面全角/半角括号差异（新增 `resolve_profit_column` 确定性表头解析）。

```text
pytest                         368 passed（比 P0 新增9项）
ruff check / format --check    OK（114 文件）
mypy src                      OK（70 文件，strict）
contracts export --check       OK（共享合同未改）
```

验收记录见 `docs/g2/p1-acceptance.md`。**距 §18.3 G2 验收仍有明确差距**：
仅 1 条指标序列 3 个所属期（要求 ≥6 条核心指标）；同比序列因未注册未入库；
taxonomy 仍为合成注册表；PMI/工业增加值/社融/上期所未采集。

## P0 工程入口（2026-10-19）

G2 之前的阻塞项已消除：此前仓库没有任何可执行业务入口，`fetch -> parse -> archive ->
publish` 只在测试里手工拼装，交易日历只有合成 fixture。

- **IP-009 CLI**：新增 `src/industrypulse/cli.py` 与 `[project.scripts] industrypulse`，
  命令 `doctor` / `source list` / `source permit` / `init` / `ingest` / `calendar validate`。
- **摄取服务**：新增 `services/ingest.py`，把"解析结果 + 原件字节"收敛为一次
  归档→构造观测→原子发布；内容指纹幂等，重新定级产生新快照而非改写历史。
- **交易日历**：新增 `core/calendar.py`，装载/校验带出处指纹的官方日历；
  拒绝空日历、重复日期、周六日与异常断层，堵住"B 级规则静默退化为 +1 自然日"。
- **证据规则收敛**：`core/evidence.py` 成为唯一实现，`ai_research.classifier`
  委托到它（对照测试守护），消除两套评级逻辑分叉的风险。
- **仓储幂等修正**：`archive()` 对同内容重复归档返回首次记录，
  不再因 `retrieved_at` 变化而报 `immutable artifact_id conflict`。

纪律保持不变且已用真实落库数据验证：默认 `value_evidence = C`，未人工核验同期原件
则 STRICT PIT 一律以 `INSUFFICIENT_VALUE_EVIDENCE` 排除；`--contemporary` 才可升格。
本机以合成 HTML 走通 permit → init → ingest → as-of 探针（09:29:59 不可见、
09:30:00 可见并选中新版）与幂等重放（`created: 0, reused: 1`）。

```text
pytest                         357 passed（比 G1 新增34项）
ruff check / format --check    OK（111 文件）
mypy src                      OK（69 文件，strict）
contracts export --check       OK（冻结生成物同步，未改共享合同）
```

操作手册见 `docs/runbooks/ingest.md`。**尚未完成**：真实官方文档的许可核验与跨月
采集、交易日历官方文件、API 15 条路由绑定、真实 OOS——即 G2 主体仍未开始。

## G1 交付

- S/A：许可状态机、可验证续传、官方表格/期货/手工导入、候选校验和审查。
- D/I：单写 DuckDB、不可变 Bronze、冻结快照、三模式 PIT、祖先及单位校验。
- E/M/R：六维缺失与覆盖率、训练期评分、目标时钟、M00..04、内层搜索、
  标签释放/purge/embargo、共同 OOS 和分年报告。
- V/U/O：合成 ETF 宇宙、分红/停牌/信号隔离、四页 UI、配置、持久幂等作业、
  健康/年龄与不可变预测快照；services/API 编排和15条冻结路由。

任务映射、入口和限制见 `docs/g1/README.md` 及各组验收记录。

## G1 本机验证（2026-10-13，Windows / Python 3.13.9）

```text
pytest                         323 passed（比 G0 新增105项），覆盖率91%
ruff check / format --check    OK（含 apps/dashboard，103文件）
mypy src                      OK（65文件，strict）
contracts export --check       OK（冻结生成物同步）
git diff --check               OK
共享合同/fixture/配置/CI diff   空，无冻结边界改动
```

含1条第三方 Starlette/httpx TestClient 弃用警告，无测试失败。
仓库仍无远端，Linux / Python 3.12 和 Actions 矩阵未在本轮实际执行。

## G0 交付清单

- [x] C-001 仓库初始化：src 布局、pyproject、双平台双版本 CI、目录骨架
      （Python 3.13 决策：任务书锁 3.12，项目所有者确认改用 3.13，区间 `>=3.12,<3.14`）
- [x] C-002 `contracts/sources`：SourceDescriptor / SeriesSpec / ReleasePolicy，
      许可与候选状态机，未知字段拒收 + roundtrip 测试
- [x] C-003 `contracts/observation`：ObservationVintage（六时间属性、双证据评级、
      修订共存）、EvidenceCandidate（§5.4 全字段）、RawArtifact、ResearchQuery
- [x] C-004 `contracts/context` + `contracts/pit`：ResearchMode 三模式证据门槛、
      as-of 选择语义（B 级次交易日 09:30、C 级保守滞后、§7.3 tie-break）；
      三模式信息集一致性由 fixture 驱动测试验证
- [x] C-005 `contracts/domain`：IndustryNode（ID 与显示名解耦）、IndicatorSpec
      （MACRO_PROXY/unknown 方向规则）、AssetMaster（PIT-008 上市判定）、MarketBar
- [x] C-006 `contracts/model`：FeatureFrame（max_ancestor_available_at 防泄漏）、
      TargetFrame（label_available_at/PIT-006）、Prediction、Repository/Model 协议
- [x] C-007 `contracts/research`：ExperimentSpec（§12.4 全 manifest 字段）、
      BacktestEvent（signal≤order≤fill，INVEST-001）、JobStatus 状态机
- [x] C-008 `contracts/http`：12 错误码 + HTTP 映射 + remediation、统一响应外壳、
      §14.2 全部 15 条路由清单；生成物由 `scripts/export_contracts.py` 导出，
      CI `--check` 守护漂移（API mock 响应由 schema 生成）
- [x] C-009 `tests/fixtures/g0/`（7 个 fixture）：三版本修订、未来发布、B/C/D 证据、
      跨年 YTD、缺失行、假日日历、ETF 晚上市/退市/未上市、指数代理、分红除息、
      证据候选 verified/FETCH_FAILED/冲突对
- [x] C-010 `docs/contracts-v1/`：发布说明、契约清单、11 lane 路径所有权表、
      mock 端点、冻结的 PIT 语义七条、`contracts/MANIFEST.json` 指纹
- [x] T-001 conftest/加载器 + coverage 配置（合同层实测 97%）
- [x] T-002 `tests/pit/test_release.py`：PIT-001/002/004/005/006/010 + 三模式信息集
      一致性 + 全局无泄漏断言（hypothesis 属性测试 200 例）
- [x] T-003 `tests/pit/test_vintage.py`：PIT-003 首发 v1 选择、当前查询取修订 v2、
      同日双版本 tie-break 确定性（50 种打乱重放）、缺失行非零填充

## 本机验证记录（2026-10-13，uv 0.12.3 / win32）

```text
uv sync --all-extras            OK（74+ 包，uv.lock 锁定）
ruff check / format --check     OK（src tests scripts）
mypy src（strict + pydantic 插件）OK（26 文件）
pytest                          218 passed
pytest --cov                    合同层 97%（export.py 82% 为 --check 路径，CI 子进程覆盖）
scripts/export_contracts.py --check  OK（84 个生成物同步）
```

CI 配置同套命令（ubuntu/windows × 3.12/3.13）；仓库尚无远端，未在 Actions 实跑。

## 已知事项 / 后续门禁

- lane 强类型报告已在自有模块定义；共享合同 Schema 收紧仍需独立版本评审
- `tests/fixtures/g0` 是全 lane mock 数据源；新增场景走共享区评审
- 正式 ADR（Python 3.13 决策）待补 `docs/adr/`
- 真实交易日历（官方休市安排）尚未入库；`core/calendar.py` 只提供装载与校验
- 规格文件（任务书 md/docx/pdf、两个 CSV、sample_configs）仍在根目录，待确认后迁移
- IP-009 CLI 入口已完成（P0）；pre-commit secret scanning 仍未做
- S/A 真源跨月样本：利润源已取得 3 期真实公告（见 `docs/g2/p1-acceptance.md`），其余 17 源仍维持 CANDIDATE
- V-001 真实主表依赖 G2 S-011，本轮仅合成宇宙工程验收
- 模型保持 NOT_VALIDATED/UNCALIBRATED/NO_EDGE，不提供投资推荐
