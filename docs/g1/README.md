# G1 十路离线核心交付

日期：2026-10-13（Asia/Shanghai）。按 parallel_backlog.csv 的 G1 和
contracts-v1 所有权实现 S/A/D/I/E/M/R/V/U/O，并补 T-004..006。
本轮验收为合成数据、离线 mock 的工程闭环。真实源许可、跨月样本、
首发历史覆盖、真实 ETF 主表、真实 OOS 和投资有效性尚未通过，不能将
候选来源升为 VERIFIED 或宣称预测优势。

- S/A：[源适配与证据审查](s-a.md)。
- D/I：[仓储、PIT 与行业映射](d-i.md)。
- M/R：[目标、模型与冻结 OOS](m-r.md)。
- E/V/U/O/API：[景气、投资边界、四页 UI 和调度](e-v-u-o.md)。

共享合同、生成物、fixture、conftest、pyproject、uv.lock 和 CI 均未修改。
AST 测试守护模块边界；S/A 无副作用 HTML 工具放 core，A 通过只读 Protocol
注入下载/解析输入，R 模型由 services 注入。

## 重放验收

```powershell
.venv/Scripts/ruff.exe check src tests scripts apps/dashboard
.venv/Scripts/ruff.exe format --check src tests scripts apps/dashboard
.venv/Scripts/python.exe -m mypy src
.venv/Scripts/python.exe scripts/export_contracts.py --check
.venv/Scripts/python.exe -m pytest --cov=industrypulse --basetemp=artifacts/pytest-g1-acceptance
```

本机默认 TEMP pytest 目录拒绝访问，使用项目 artifacts 内独立 basetemp。
并行测试需使用不同路径，临时路径不得存放业务数据。

最终本机结果：Windows / Python 3.13.9，323 passed，覆盖率91%；ruff lint/format
103文件通过，strict mypy 65文件通过，合同同步与 git diff --check 通过。
存在1条第三方 TestClient 弃用警告；Linux/3.12/远端 CI 尚未实际执行。

集成证明冻结原件/快照 → 景气/API，以及按 m+3 标签公布时钟训练 M00..04 →
共同 OOS/分年误差 → 不可变报告；四页 UI 无后端可运行。G2 接入仍需完整
source quality gates，保留 INSUFFICIENT_HISTORY / UNSCORABLE / NO_EDGE。

## 明确差异与待验收项

- V-001 对 G2 S-011 的依赖用 AssetMaster 与合成宇宙先验证，真实主表待接入。
- TargetSpec 与25候选位于 M 自有 features，未写未分配的 config/targets。
  候选定义为名称和祖先协议，具体真源派生公式/口径需正式 series inventory
  验证后配置，不能用缺失列顶替真实数据。
- lane 强类型报告未迁入共享 contracts，Schema 收紧须独立版本评审。
- 未安装系统定时任务和真实搜索/LLM 服务；JSONL 审核日志仍为单进程，
  并发审核与 Silver 发布在后续 A-012 集成。
