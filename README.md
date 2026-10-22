# IndustryPulse

行业经济监测、预测与投资研究系统。本地优先、Python 模块化单体；经济预测与 ETF 投资验证两条链独立汇报。

**当前状态：G3 工程验收已通过，真实数据研究与 ETF 回测可运行；预测优势仍未验证。
新增现代化行业情报 dashboard，支持资讯 API / JSON 接入。**

## 可视化工作台

双击根目录的 `启动IndustryPulse.cmd`，自动启动后台并打开独立应用窗口。
保留启动控制台；关闭控制台或按 Ctrl+C，会同时结束界面、服务及后台子进程。
关闭独立应用窗口也会退出后台。默认地址 http://127.0.0.1:8765 。
包含领域总览、趋势与资金、资讯、人事与公司动态、收藏、
本地真实研究数据与数据接入设置。新增宏观、六维景气、八类模型预测、滚动实验、
ETF 回测、证据审查和运维任务。资讯默认虚构演示；研究页直接使用本地真实数据。
使用见 [运行手册](docs/operations.md)，[交付对账](docs/completion-20261022.md)。

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

- `src/industrypulse/`：冻结合同、G1 十路业务实现与 API/服务编排
- `contracts/`：冻结契约生成物（JSON Schema / OpenAPI 样例 / 清单）
- `config/`：行业 / 目标 / 特征 / 模型 / 研究 / 数据源 / 质量规则配置
- `apps/dashboard/`：Streamlit 前端（lane U）
- `tests/`：contracts / pit / unit / research / integration / fixtures
- `docs/`：architecture / adr / api / data_sources / research_protocols / runbooks / contracts-v1
- `scripts/`、`migrations/`、`planning/`、`reports/`
- `data/`、`artifacts/`：本地生成，不入 Git

## G1 离线工作台

```powershell
.venv/Scripts/python.exe -m streamlit run apps/dashboard/app.py
.venv/Scripts/python.exe -m pytest tests/integration --basetemp=artifacts/pytest-g1-integration
```

前端使用冻结合成 fixture，含三模式和四页。后台入口为 industrypulse.api.app:app，
未绑定业务 handler 的端点返回结构化不可用状态；PipelineService 编排实际
仓储/景气/模型/研究。任务映射和限制见 [G1 交付记录](docs/g1/README.md)。
