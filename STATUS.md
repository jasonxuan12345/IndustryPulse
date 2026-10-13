# STATUS — IndustryPulse

最后更新：2026-10-13（Asia/Shanghai）

## 当前阶段

**G0 契约冻结完成**（C-001..C-010、T-001..T-003 全部验收；git 标签 `contracts-v1`）。
下一步：G1 十路并行（S/A/D/I/E/M/R/V/U/O + T 横向），按 `docs/contracts-v1/README.md`
的路径所有权表开工。

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

## 已知事项 / G1 提醒

- ApiResponse.result 的 result_kind 强类型由各 lane G1 提 PR 收紧（G0 冻结外壳）
- `tests/fixtures/g0` 是全 lane mock 数据源；新增场景走共享区评审
- 正式 ADR（Python 3.13 决策）待补 `docs/adr/`
- 规格文件（任务书 md/docx/pdf、两个 CSV、sample_configs）仍在根目录，待确认后迁移
- IP-009 CLI 入口、pre-commit secret scanning 未做（不阻塞 G1）
