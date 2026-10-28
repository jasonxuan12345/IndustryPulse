# AI 投资研究 MVP 交付说明

## 前端入口（2026-10-28 补齐）

启动应用后打开 [跨行业 AI 研究](http://127.0.0.1:8765/research)，也可从工作台左侧进入。
页面直接读取冻结研究归档，展示行业覆盖、资产映射、建议状态、原文证据及发布时间、独立反证缺口、估值方法和观察/失效/退出条件；支持搜索、建议筛选、切换归档、下载简报与建议。

“运行新研究”提交后台任务，显示排队/执行/完成或失败状态，页面无需等待长请求；重复点击复用当前任务。浏览归档不会发起模型调用。原量化研究页保留在 [量化模型与准入](http://127.0.0.1:8765/research/quant)。

总览不再为缺失行情、资金流、基准或商品数据保留空图卡片；缺项汇总展示原因、状态更新时间及刷新/接入入口。有数据的图表保留，真实零值正常展示。公司未获取的行情和估值会注明待更新，不制造目标价或收益。

前端专项验收见 `docs/mvp-ui-acceptance.md`。现有运行服务已加载新接口，原8765地址可直接使用。

本增量在现有 IndustryPulse 上复用 20 行业配置、PIT 选择器、ETF 价格特征、风险门槛和冻结前瞻账本，增加独立的 `AI_RESEARCH` 链路。原 T1/T2/T3、费用、分红和收益验证模块保持原样。

## 安装与运行

依赖沿用 `pyproject.toml` / `uv.lock`，Python 3.12–3.13：

```powershell
uv sync --all-extras
.\.venv\Scripts\python.exe -m industrypulse.cli research-mvp
```

本地数据库 `data/g2.duckdb` 存在时提供统计与行情输入；首装缺少数据库时可用已采集新闻运行，并显式记录历史输入缺口。命令每次扫描全部20行业，按来源、映射和发布时间排序，每行业最多一条、全局最多5条，不足不凑数。月度线索使用可见发布日期，行情线索使用收盘交易日；不会将统计所属月当作发布日期。

输出在 `artifacts/research-mvp/<run_id>/`：

- `frozen_inputs.json`：全部行业冻结输入与有效配置。
- `opportunity_queue.json`：覆盖情况、触发、输入、排名依据和研究问题。
- `evidence_packet.json`：官方页面原文、hash、事实、摘录字符位置、时间及支持/反对关系。
- `research_review.json`：支持与反对独立搜索、判断、调用日志与缺口。
- `investment_memo.json`：资产暴露、论点、估值缺口、悲观/基准/乐观条件、入场/失效/退出条件。
- `daily_report.md`、`alerts.json`、`manifest.json`：简报、降级告警与完整性摘要。
- `tool_archive.json`、`checkpoints/`：工具结果归档和逐机会断点。

默认写入既有 `data/ledger/recommendations.jsonl`，以 `run_id + opportunity_id + asset_id + horizon` 幂等登记1/3/6个月观察窗口，沿用原手续费和分红政策。实验可通过 `--ledger-path` 指定独立账本。现有账本不是本次交付样例的写入目标；样例使用独立账本，避免把验收运行当作正式观察。

## 模型与检索配置

连接复用设置页保存的 `data/model-provider.json`，不打印或复制密钥。配置示例见 `config/research/mvp.yaml`；支持 `--config PATH` 覆盖。

2026-10-28 对当前已配置官方服务的 `/models` 实测返回 `deepseek-flash` 与 `deepseek-v4-pro`；`deepseek-v4.1-flash` 返回 HTTP 400。这些是历史验收的实际别名；当前默认配置的 `model` 为空，会沿用用户设置或环境变量，**无法声称已固定到 V4.1 权重版本**。服务提供版本ID后可仅修改 `model`，不需要改代码。

搜索与页面/公告正文读取采用可配置 JSON HTTP 接口，默认 DeepSeek Search/Fetch。请求与响应规范参考[官方 Search API](https://api-docs.deepseek.com/api/search/)与[Search/Fetch 指南](https://api-docs.deepseek.com/guides/search/)。其他供应商需兼容以下形状：搜索返回 `results`（含 `url/title/date`），读取返回 `title/content`。可用 `INDUSTRYPULSE_SEARCH_KEY` 单独指定检索密钥，未设置时仅在协议、主机及端口相同的来源复用模型密钥；其他供应商必须独立配置，不会跨来源发送旧密钥。

模型先提出查询，检索限定官方域名，再读取全文并抽取逐字摘录；反对检索只拿研究问题，不拿支持步骤的解释。最多3轮，每条24次工具/模型调用、单次30秒、每条240秒预算；检查在每次调用前执行，正在执行的最后一次调用最多额外占用一个请求超时。失败和非JSON输出显式降级并继续下一个行业。首轮以统计局为原始来源，后续轮次可轮换到巨潮公告；`primary_search_domains` 可配置。

证据的 `primary_text_matched` 只代表摘录在所读官方正文中出现；经济解释和支持/反对关系仍需人工复核。搜索索引日期及事后抓取均标为 `RECONSTRUCTED`，不冒充历史严格 PIT。未知日期和研究时点之后的资料不入证据包。

## 复现与持续执行

```powershell
# 指定四行业优先级，但仍扫描全部20行业
.\.venv\Scripts\python.exe -m industrypulse.cli research-mvp --config config/research/mvp-cases.yaml --run-id my-cases

# 用交付样例的冻结输入与工具归档离线回放；不发出网络请求
.\.venv\Scripts\python.exe -m industrypulse.cli research-mvp --replay examples/research-mvp/frozen_inputs.json --run-id offline-review --ledger-path artifacts/research-mvp/offline-review-ledger.jsonl

# 北京时间工作日18:10触发，必须命中已知交易日历
.\.venv\Scripts\python.exe -m industrypulse.cli research-mvp --schedule

# 自动化回归
.\.venv\Scripts\python.exe -m pytest --basetemp=artifacts/mvp-pytest-local --disable-warnings
.\.venv\Scripts\python.exe scripts/export_contracts.py --check
.\.venv\Scripts\python.exe scripts/verify_mvp_delivery.py
```

同一运行失败后，用相同 `--run-id`、`--as-of` 和配置重跑；已完成机会直接读取断点，已入账记录不重复。已完成运行复查冻结文件hash；要用新数据重新研究，使用新ID。离线回放缺少工具归档时标为 `unverified`，不回退联网、不伪称复现成功。

调度已接入项目原有的 `configure_schedule`：启用工作台调度后，每个已知交易日19:10执行本研究链路，原18:10采集与月末18:40预测保留。独立命令 `--schedule` 则在18:10执行；选用一种启动方式即可。进程需持续运行；关闭控制台或 Ctrl+C 停止，文件与账本保留。交易日判断复用 `data/shfe-calendar-2026.json`；缺少日期时记录 `skipped_unknown_or_closed_calendar`，不把工作日直接当成交易日。后续年份需更新日历。失败告警以本地 JSON/JSONL 留存，不自动向外发送消息。

## 结论边界与故障排查

收益有效性仍是 `NOT_VALIDATED`。现阶段 ETF 估值适配器缺少可核验的同期成分权重、PE/PB 和情景输入，因此当前建议会降级到 `WATCH`（无映射为 `REJECT`），收益和目标价留空。原模型是否通过 T3 不会阻止生成这些研究记录。没有自动实盘订单接口；研究建议保留人工审核字段。

- API 400：检查服务 `/models` 的真实模型ID；不要自行假定版本名称存在。
- API 401/402/429/超时：修复连接或额度后用新运行ID重查；已完成的降级结果保持冻结，避免改写历史。
- 没有官方证据：看 `research_review.json` 的独立搜索、日期过滤、读取与提取记录；不得把媒体摘要当作核实正文。
- 数据库被占用：退出其他写入任务，重跑同一失败运行。冻结输入不会被新数据替换。
- 账本完整性错误：停止写入，从已核验备份恢复；程序不会跳过损坏行。
- Windows pytest 临时目录拒绝访问：使用上述项目内唯一 `--basetemp`，不要修改生产目录权限。

验收记录与真实输出见 `acceptance_report.md`。全仓已有静态检查问题单独列出，未为本次增量改写无关模块。

跨平台启动、`.env` 与兼容API设置请优先参考 [部署与配置](docs/deployment.md)。历史验收日志中的 Windows 命令可将解释器替换为 macOS/Linux 的 `.venv/bin/python`。
