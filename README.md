# IndustryPulse

行业经济监测、预测与投资研究系统。本地优先、Python 模块化单体；经济预测与 ETF 投资验证两条链独立汇报。

**当前状态：G0 仓库骨架（C-001）。契约（C-002+）、业务模块与 CLI 均未开始，见 STATUS.md。**

完整规范：`IndustryPulse_开发任务书_V2.0.md`（IP-SRS-002）；任务清单：`parallel_backlog.csv`（136 项）；数据源清单：`source_catalog.csv`（18 源）。

## 环境要求

- Python 3.12（任务书锁定 `>=3.12,<3.13`；本机仅有 3.13，本地安装验证前需先装 3.12 或记 ADR）
- 依赖约束见 `pyproject.toml`；`uv.lock` 尚未生成

## 开发命令（干净环境实跑记录见 STATUS.md）

```powershell
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
ruff check .
ruff format --check .
mypy src
pytest
```

## 目录

- `src/industrypulse/`：包源码（src 布局，13 个子包占位）
- `contracts/`：冻结契约层（C-002..C-008 产出），并行开发的唯一跨 lane 依赖
- `config/`：行业 / 目标 / 特征 / 模型 / 研究 / 数据源 / 质量规则配置
- `apps/dashboard/`：Streamlit 前端（lane U）
- `tests/`：unit / contract / integration / pit / research / fixtures
- `docs/`：architecture / adr / api / data_sources / research_protocols / runbooks
- `scripts/`、`migrations/`、`planning/`、`reports/`
- `data/`、`artifacts/`：本地生成，不入 Git
