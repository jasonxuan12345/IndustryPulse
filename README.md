# IndustryPulse

本地优先的行业情报、AI 经济研究与量化辅助分析工作台。

## 核心方向

- **行业新闻与行情**：聚合行业公开资讯源、板块行情、宏观指标与重点公司/ETF 信息。
- **AI 经济分析与机会发现**：并行阅读新闻与产业链线索，识别潜在传导链与早期变动，辅助筛选重点关注板块。
- **量化辅助依据**：提供板块市场位置、拥挤度、波动率及统计模型基线作为客观参考。

## 环境准备与开发运行

需要 **Python 3.12 或 3.13**。

```sh
python -m venv .venv
# Windows:
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
# macOS / Linux:
.venv/bin/python -m pip install -e ".[dev]"
```

配置 `.env`（参考 `.env.example`），在开发调试阶段可直接使用 Uvicorn 启动服务：

```sh
uvicorn industrypulse.api.runtime:app --host 127.0.0.1 --port 8765 --reload
```

访问 `http://127.0.0.1:8765/`。

## 校验与测试

```sh
python -m ruff check src tests scripts
python -m ruff format --check src tests scripts
python -m mypy src
python scripts/export_contracts.py --check
python -m pytest --basetemp=artifacts/pytest-local --disable-warnings
node scripts/check_dashboard_states.cjs
```

## 目录结构

- `src/industrypulse/`：后端服务、AI 研究、市场行情、量化与数据存储模块
- `apps/dashboard/web/`：Web 前端静态页面与脚本
- `config/`：行业、资讯源、行情与研究配置
- `contracts/`：自动导出的 OpenAPI 与 JSON Schema 合同
- `docs/`：接口与配置说明文档
- `tests/`：单元、合同与集成测试

本项目以 [MIT](LICENSE) 发布。研究结论仅供参考，不构成投资建议。
