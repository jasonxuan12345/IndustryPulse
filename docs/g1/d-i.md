# G1 D / I 仓储与行业知识

D-001..010 和 I-001..005 在冻结 contracts-v1 下实现；未修改共享合同、fixture、conftest 或工程依赖。

## 接入入口

`industrypulse.repositories` 导出 `Database`、`DuckDBObservationRepository`、
`PublicationError`、`RepositoryQuery`、`convert_value`。
`industrypulse.taxonomy` 导出 `TaxonomyRegistry`、`SecuritiesIndustryLink`、
`load_steel_taxonomy`。

使用顺序：注册 SourceDescriptor → 注册 SeriesSpec → register_taxonomy(nodes, indicators)
→ archive(RawArtifact, bytes) → 可选 register_candidate(EvidenceCandidate)
→ publish(snapshot_id, vintages) → fetch(series_ids, AnalysisContext)。
`publish` 要求每条观测有 `artifact_id`，原件来源必须与 series 一致；含 AI candidate
的观测同时验证 verified 状态、哈希、数值和单位。G0 利润 fixture 的原件大多未设置，
测试归档明确合成的文本，再复制观测设置对应 artifact_id；没有伪造真实采集证据。

## 仓储纪律

- D-001：每个 DuckDB 操作先持有进程内 RLock，再获取独立 SQLite 的
  `BEGIN IMMEDIATE` writer lease；持有 lease 才打开 DuckDB，关闭 DuckDB 后才释放。
  读操作同样串行，防止跨进程连接锁冲突。迁移可重复；禁止绕过 Database 直连。
- D-002：内容寻址 Bronze 路径以 sha256 分片，`xb` 创建、不覆写；hash 与 byte_size
  核对。相同字节的不同 artifact ID 共用文件。发布时重新校验原件完整性。
- D-003/004/009/010：Silver 的 `(series_id, observation_start, vintage_id)` 唯一。
  注册、原件链路、单位/scale/币种、行业有效期和统计标准都在同一发布事务中校验。
  任意行失败全部回滚；snapshot ID + 内容哈希相同重放等价，ID 相同内容变化拒绝。
  各 snapshot 存储精确版本集合；传入 AnalysisContext.dataset_snapshot_id 的查询
  必须从该集合选取，未知快照拒绝，避免后续修订污染已冻结实验。
- D-005/006：冻结纯函数决定证据门槛与 effective_available_at，DuckDB SQL window
  执行同周期 `(effective_available_at DESC, vintage_id DESC)` 选择。
  B 日期级发布时间按冻结日历规则处理，C 在 RECONSTRUCTED 叠加自然日滞后。
  构造器接受 `conservative_lag_days`（默认 5）、`policy_version` 和
  `series_lag_days`（已验证历史 p99/safety 的系列配置），支持 +5/+15 场景重放。
- D-007：`query()` 返回 selection + policy version + 默认/系列 lag + pit_verified
  + watermark；EXPLORATORY 强制提示 `not PIT verified; NOT investment proof`。
  协议 `fetch()` 返回冻结 ObservationFrame，保留 research_mode 字段。
- D-008：record_ancestry 冻结所有叶观测键；record_derived_ancestry 将父特征递归
  闭包展开并去重。ancestry 返回每个实际输入和按当前模式计算的最大可用日；
  未来输入、低等级输入、快照外输入拒绝。特征 ID 不可重新绑定祖先。

Silver 发布失败可以留下已经合法归档但未发布的 Bronze 原件，这是只增不改归档的
预期行为；不会覆盖上次合格 Silver 快照。`convert_value` 只接受显式登记且维度兼容
的 Decimal 单位（元/万元/亿元，吨/万吨等），None 保持缺失，负数和真实零保留。
入库不自动换单位：解析线须显式换算，观测必须与 SeriesSpec 完全匹配。

## 行业配置边界

`config/industries/steel.yaml` 使用 YAML 1.2 兼容 JSON 语法，内容派生自冻结 G0
合成 fixture，明确 `synthetic: true`，版本 `steel-g1-synthetic-v1`。
G0 的产业关系及 ETF 是演示输入，并非已验证真实产业链、证券代码或交易映射。
source_ref 指向合成依据；不新增无法核验的真实 ETF/行业权重。

I-001 稳定 CN_NBS_C31 身份不依赖显示名；I-002 关系携带依据/有效期；I-003
制造业 PMI 必须同时在 series tags 和 indicator/mapping role 明确 MACRO_PROXY；
I-004 NBS 与 CSI 节点标准分开，通过显式 proxy link 与 asset IDs 三方关联，
指数以 INDEX 注册、不可假冒 ETF。I-005 拒绝重复 ID、未知节点/系列/资产、父级
环、单位不兼容、未登记跟踪指数、失配宏观标签及不合法日期。

## 验证

单元和 T-006 属性测试覆盖三模式 × 三 as-of × +5/+15，与冻结 PIT 完整
selected/skipped 等价；原子回滚、幂等、不可变快照、原件失链/篡改、候选数值
冲突、线程/进程 writer、祖先闭包、宏观代理、跨标准映射及亿/万正负/零/缺失换算。

```powershell
.venv/Scripts/python.exe -m pytest tests/unit/repositories tests/unit/taxonomy tests/data --basetemp=artifacts/pytest-di-verification
.venv/Scripts/python.exe -m ruff check src/industrypulse/repositories src/industrypulse/taxonomy tests/unit/repositories tests/unit/taxonomy tests/data
.venv/Scripts/python.exe -m mypy src/industrypulse/repositories src/industrypulse/taxonomy
```

本机默认用户 TEMP 中已有 pytest 目录权限受限，验证用独立项目内 basetemp。
G2 的批量 Parquet SDK、修订差异报表、备份恢复和真实关系/ETF覆盖审计尚未实现。
