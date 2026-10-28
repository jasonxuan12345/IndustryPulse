# AI 投资研究 MVP 验收报告

前端已于同日补齐：新增研究门户、归档/证据/建议展示、空状态与后台运行操作；原8765服务已更新。新增验收为 **623 passed, 1 skipped**，详见 [前端专项验收](docs/mvp-ui-acceptance.md)。下文619项是前端补齐前的原始验收记录，保留历史真实输出。

日期：2026-10-28（北京时间）。交付基于原 IndustryPulse 增量实现，未改预测模型、T3、合同与收益核算代码。以下 PASS 代表任务书七项工程闭环验收，不代表收益有效性或买入推荐已获验证。

## 七项验收

1. **PASS — 多领域**：真实运行扫描20行业，四案例覆盖煤炭 `CN_NBS_C06`、油气 `CN_NBS_B07`、农副食品 `CN_NBS_C13`、专用设备 `CN_NBS_C35`。四行业均完成队列→搜索/阅读→证据→建议→1/3/6月前瞻观察，真实结果均为 WATCH。
2. **PASS — 自主查证与降级**：使用当前已配置 DeepSeek 服务自主提出搜索词，支持/反对分别检索，读取官方正文。四案例合计7条摘录通过逐字匹配、文档hash与位置校验。存在读取失败和未完成的反证，逐项保留 `unverified` 与日志，不伪称核实。真实检索和模型探测见 `artifacts/mvp-live-probe.json`。
3. **PASS — 完整研究结论**：四条 memo 均包括观察资产、业务暴露、传导论点、估值方法与缺口、悲观/基准/乐观假设、入场、失效、退出条件及风险。缺少充分估值输入，目标价与收益情景均为 null；没有硬凑买入。人工审核与禁止执行字段明确。
4. **PASS — 无未来证据**：交付验证逐条断言 `publish_time <= as_of`；新增测试覆盖未来日期、未知日期、伪造摘录。搜索索引日期和事后抓取明确为 `RECONSTRUCTED`，不宣称严格历史 PIT。
5. **PASS — 幂等与失败留痕**：连续3个运行生成36条1/3/6月观察记录；各自重复运行均字节不变。复用原冻结hash链账本。支持逐机会断点、工具结果归档、账本失败后续跑；异常只记类型，避免密钥进入日志。
6. **PASS — 原有能力不退化**：原基线 **607 passed, 1 skipped**；最终全套 **619 passed, 1 skipped**。新增12项回归。合同生成物检查通过；预测、T3、分红费用及冻结账本实现未修改。
7. **PASS — 可持续运行**：新增 `research-mvp` 命令，接入原 APScheduler 调度（已知交易日19:10），另支持18:10独立调度。真实工具归档在禁用网络后连续回放3次；注入1次搜索失败仍完成四行业建议，生成每日简报与本地告警。日历未知时跳过并留痕。

## 可复核命令及真实输出

原测试基线：

```text
.venv\Scripts\python.exe -m pytest --disable-warnings --basetemp=artifacts/mvp-pytest-baseline
607 passed, 1 skipped, 47 warnings in 42.59s
```

最终全套：

```text
.venv\Scripts\python.exe -m pytest --disable-warnings --basetemp=artifacts/mvp-pytest-acceptance
619 passed, 1 skipped, 47 warnings in 48.98s
```

完整输出：`artifacts/mvp-baseline-retry.txt`、`artifacts/mvp-acceptance-tests.txt`。首次默认临时目录遇到 Windows `PermissionError`，已通过项目内独立 basetemp 解决；原始失败记录保留在 `artifacts/mvp-baseline-tests.txt`，未隐藏环境故障。

增量静态检查：

```text
ruff check research_mvp.py workbench_ops.py test_research_mvp.py probe_mvp.py verify_mvp_delivery.py
All checks passed!
mypy src/industrypulse/services/research_mvp.py src/industrypulse/services/workbench_ops.py
Success: no issues found in 2 source files
python scripts/export_contracts.py --check
contracts 生成物与源合同同步
```

真实案例：

```powershell
.\.venv\Scripts\python.exe -m industrypulse.cli research-mvp --as-of 2026-10-28T19:00:00+08:00 --run-id delivery-cases --config config/research/mvp-cases.yaml --ledger-path artifacts/research-mvp/delivery-ledger.jsonl
```

产物：`artifacts/research-mvp/delivery-cases/`，日志：`artifacts/mvp-delivery-cases.txt`。四案例配置显式提高指定四行业优先级；仍扫描20行业，不伪称这四个恰好是无约束排名前四。此前另做一次无行业优先级的全量真实运行，见 `artifacts/research-mvp/live-20261028/`。

离线连续执行、幂等与故障注入：

```text
.venv\Scripts\python.exe scripts/verify_mvp_delivery.py
status: PASS
scanned_sectors: 20
real_cases: 4
primary_text_matched_facts: 7
offline_runs: 3
continuous_ledger_records: 36
idempotent: true
future_evidence_rejected: true
unmatured_labels_unfilled: true
fault_injected: true
other_sectors_continued: true
network_disabled_during_replay: true
```

原始 JSON 输出：`artifacts/mvp-delivery-verification.json`；完整日志：`artifacts/mvp-verification-output.txt`。故障注入使用真实已归档资料，合成单次连接失败；这部分没有伪称为第二次实时联网采集。

## 外部限制及未宣称通过的项目

- **精确固定 DeepSeek V4.1 权重版本：FAIL（服务未提供可核验ID）**。当前账号 `/models` 仅提供 `deepseek-flash` 与 `deepseek-v4-pro`；调用 `deepseek-v4.1-flash` 返回400。已使用可用 Flash 别名并记录，不将它冒充固定的 V4.1 版本。
- **投资收益有效性、可执行买入与数值估值：未验收**。本轮全部 WATCH，未将统计相关性或模型文字当成收益证据；ETF穿透估值输入仍不足。当前估值适配器会阻止 BUY_CONDITIONAL；无实盘交易接口。
- **全仓静态检查：FAIL（既有问题）**。全仓 ruff 仍有11个既有问题，记录于 `artifacts/mvp-lint.txt`，涉及原有 `verify_data_coverage.py`、`analytics/heat.py`、`api/dashboard.py`、`market/providers/tencent.py` 等。增量文件检查通过；未扩大范围修复无关代码。
- 调度已注册并验证触发配置，但没有伪称已在后台持续运行几天。进程需保持开启；日历目前复用2026年已捕获交易日，未知日期不执行。
- 证据的正文匹配不等同于经济因果成立；四案例独立反证与估值仍有缺口，见逐条 `data_quality.gaps`。

运行、配置、停止与故障处理见 `README_MVP.md`。交付包为当前项目的增量代码与可离线复现的真实样例，不包含用户API密钥或生产数据库。
