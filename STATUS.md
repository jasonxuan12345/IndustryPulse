# STATUS — IndustryPulse

最后更新：2026-10-13（Asia/Shanghai）

## 当前阶段

G0 工程基座与契约冻结（进行中）

## 已完成

- [x] C-001 仓库初始化：git init（main）、src 布局（13 个子包占位 + py.typed）、
      pyproject（依赖 / ruff / mypy strict / pytest 配置）、GitHub Actions 双平台 CI、
      目录骨架（contracts/config/data/apps/tests/docs/scripts/migrations/planning/reports/artifacts）
  - 本地已实跑：ruff check / ruff format --check / mypy src / pytest（见当日提交备注）
  - 未实跑：`pip install -e .`（本机无 Python 3.12，requires-python 锁死 <3.13）、CI（未推远端）

## 未开始（按 parallel_backlog.csv 顺序）

- C-002..C-010：契约冻结与 fixtures
- T-001..T-003：测试框架规范与 PIT / vintage 负例
- IP-009：CLI 入口（`industrypulse --help`）
- uv.lock 生成；pre-commit secret scanning
- 根目录规格文件（任务书 md/docx/pdf、两个 CSV、sample_configs）尚未按
  README_实施包 建议归位到 docs/planning/config——待确认后再迁移

## 已知阻塞

- 本地 Python 为 3.13.12（managed）/ 3.13.9（anaconda），任务书要求 3.12；
  `pip install -e .` 验证需先安装 3.12 或按任务书 §3.1 流程记录 ADR
