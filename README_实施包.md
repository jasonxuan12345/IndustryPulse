# IndustryPulse V2.0 实施文件说明

本包不是已经建好的投资模型，而是从零开发用的**规格文件、验收任务、数据源清单和配置模板**。

## 文件
- `IndustryPulse_开发任务书_V2.0.md`：主开发规范，作为仓库 `/docs/` 正本。
- `IndustryPulse_开发任务书_V2.0.docx`：便于审阅的 Word 版。
- `parallel_backlog.csv`：136 个可分配的独立任务；字段包括 lane / phase / depends_on / deliverable / acceptance。可直接导入 Issue 管理软件。
- `source_catalog.csv`：18 个真实来源或待核验候选源。已确认“页面存在”不代表稳定公开API或合法批量下载已通过。
- `sample_configs/`：钢铁行业、利润率研究目标和基准模型的最小配置模板。须在真实数据清点后冻结参数。
- `build_summary.json`：工作任务计数等基本产出校验。

## 建议启动顺序
1. 建立仓库，将主 MD 放入 `docs/`、csv 放入 `planning/`、示例配置放入 `config/`。
2. 首先交付 `C-001..C-010` 的公共模型合同和 `T-001..T-003` 的负例测试。
3. 锁定合同版本后，分多个 Codex worktree 并行认领 `S`、`A`、`D`、`I`、`E`、`M`、`R`、`V`、`U`、`O`、`T` 工作线。
4. 所有AI搜索结果都必须保留官方原件、来源与同期期数/发布日期证据，无法核实时标记候选，不自动当作原始事实。
5. 项目工程验收与量化投资收益验收分别进行，后者没有保证。
