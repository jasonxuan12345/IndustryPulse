# IndustryPulse

行业经济监测、预测与投资研究系统。本地优先、Python 模块化单体；经济预测与 ETF 投资验证两条链独立汇报。

**当前状态：G0 契约冻结完成（C-001..C-010、T-001..T-003，标签 `contracts-v1`）。
G1 十路并行开发可启动，见 STATUS.md 与 `docs/contracts-v1/`。**

完整规范：`IndustryPulse_开发任务书_V2.0.md`（IP-SRS-002）；任务清单：`parallel_backlog.csv`（136 项）；数据源清单：`source_catalog.csv`（18 源）。

## 环境要求

- Python 3.13（项目所有者确认采用；兼容区间 `>=3.12,<3.14`，CI 双版本矩阵）
- 依赖已锁定：`uv.lock`（uv ≥0.12）

## 开发命令（uv 工作流）

```bash
uv sync --all-extras              # 安装全部依赖（含 dev）
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run mypy src
uv run pytest --cov=industrypulse --cov-report=term-missing
uv run python scripts/export_contracts.py --check   # 合同生成物同步守护
```

## 合同层（并行开发的唯一跨 lane 依赖）

- 可执行真值：`src/industrypulse/contracts/`（Pydantic v2，`CONTRACTS_VERSION=1.0.0`）
- 生成物（勿手改）：`contracts/schemas/`、`contracts/openapi/`、`contracts/MANIFEST.json`
- 合成 fixtures（全部 lane 的 mock 数据源）：`tests/fixtures/g0/`
- 冻结发布说明 / 路径所有权表 / mock 端点：`docs/contracts-v1/README.md`
- 变更流程：改模型 → 递增版本 → 重跑导出脚本 → 单独 contracts PR

## 目录

- `src/industrypulse/`：包源码（src 布局；contracts 已实现，其余子包为 lane 占位）
- `contracts/`：冻结契约生成物（JSON Schema / OpenAPI 样例 / 清单）
- `config/`：行业 / 目标 / 特征 / 模型 / 研究 / 数据源 / 质量规则配置
- `apps/dashboard/`：Streamlit 前端（lane U）
- `tests/`：contracts / pit / unit / research / integration / fixtures
- `docs/`：architecture / adr / api / data_sources / research_protocols / runbooks / contracts-v1
- `scripts/`、`migrations/`、`planning/`、`reports/`
- `data/`、`artifacts/`：本地生成，不入 Git
