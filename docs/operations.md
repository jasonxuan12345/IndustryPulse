# 本地运行与故障处置

## 启动

双击项目根目录的 `启动IndustryPulse.cmd` 即可。启动后自动打开独立 Edge / Chrome
应用窗口，不使用或关闭你的普通浏览器窗口。启动控制台是整个程序的总开关：

- 关闭启动控制台，或在其中按 Ctrl+C：结束前台窗口、后台服务、定时采集及预测子进程。
- 关闭独立应用窗口：后台也随之退出。
- 重新启动后沿用数据、API / 模型设置和定时任务配置；运行中的未完成任务不会继续执行。
- 启动失败查看控制台，以及 `artifacts/dashboard/server.log`。端口占用时不会擅自结束其他程序。

命令行入口（与双击相同）：

```powershell
./scripts/start-dashboard.ps1
# http://127.0.0.1:8765
# 自定义端口；无界面运行可另加 -NoBrowser
./scripts/start-dashboard.ps1 -Port 8766
```

解释器按顺序选择：`-PythonPath` 参数或环境变量 `INDUSTRYPULSE_PYTHON`（可以是
python.exe，也可以是 conda 环境目录，脚本自动定位其中的 python.exe）→
`data/launcher.json` 中保存的选择 → 项目 `.venv`（`uv sync` 创建）→
PATH 上的 `python` / `py`（通过版本探测才采用，Microsoft Store 占位程序会被自动排除）。
没有任何可用解释器且控制台可交互时，会提示直接粘贴路径：验证通过后保存到
`data/launcher.json`（不入 Git），后续启动自动使用；`-Prompt` 可随时重新选择。
参数可以从 cmd 启动器透传，例如 `启动IndustryPulse.cmd -Port 8766`。

默认读取 `data/g2.duckdb`，由 `industrypulse.api.runtime:app` 启动真实旧版 API 与新工作台。
配置 `INDUSTRYPULSE_DASHBOARD_DB` 可切换数据库。安装使用 `uv sync --all-extras`，包含 LightGBM。
无需模型 API；八类统计模型均在本机运行。LightGBM 使用独立进程解决 Windows 与 DuckDB 的运行库冲突。

## 工作流

1. 宏观总览 / 行业景气：选择行业、模式、研究时点、快照，查看可见数据与三模式差异。
2. 模型预测：选择 T1 指标方向或 T2 同年利润率改善，选择模型后运行。结果存入 `artifacts/workbench/forecasts`。
3. 研究实验：冻结训练截止、特征和模型，按半年扩展窗滚动训练。报告包含共同 OOS、分年指标、bootstrap 区间和失败模型。
4. ETF 回测：选择标的、基准、信号序列及成本；查看净值、风险和逐笔成交。经济模型概率与基线交易规则相互独立。
5. 证据审查：导入候选或搜索公告，核对原件后批准/拒绝并记录理由。批准不会自动改写事实；可导入 ObservationVintage JSON 经已有发布校验后入库。
6. 数据与任务：导出/导入含原件的数据包、查看修订、执行采集、创建备份、恢复到独立库、启停定时任务。

后台任务在 `artifacts/workbench/jobs.sqlite`，结果在 `results/`；失败时保留错误原因及上次成功报告。
命令行可以用 `industrypulse workbench-run forecast --options-file options.json`；action 可选 forecast/experiment/backtest/backup/expand/holdout。

## 调度

数据与任务 → 采集与研究工具 → 开启每日采集与月末预测。
每天上海时间 18:10 捕获官方公告，月末 18:40 运行预测。本地服务需要常驻；配置保存在 `schedule.json`，重启恢复。
关闭按钮会停止调度，已保存数据不删除。捕获使用原有 `as-published auto`，只有符合 48 小时证据条件才升级同期证据。

## 外部接口

### 界面配置默认 AI 模型

进入“数据接入与设置” → “AI 模型与 API”，填写 Base URL 和 API Key。
离开输入框自动读取模型列表，也可以点“获取可用模型”；选择模型后点“测试所选模型”，
再点“保存并设为默认”。保存即时生效，重启沿用，不需要再配置环境变量。
服务商没有模型列表时，可手动输入模型 ID。支持 OpenAI 兼容 Chat Completions 和 Responses 协议。

配置保存到本机 `data/model-provider.json`（不入 Git）；重新打开设置不会回显密钥，
留空沿用同一地址已保存的密钥，换服务商需要填写对应 Key。
`INDUSTRYPULSE_MODEL_CONFIG` 可指定配置文件位置。
停用开关保留配置并停止使用它，不会偷偷切换到旧的抽取接口。

新闻详情“使用默认模型解读”和证据候选抽取共用此配置；本地统计预测模型继续独立运行。
模型 API 不等同于实时新闻/行情或独立搜索 API。模型列表仅代表接口声明的可见模型，
实际文本权限和额度以“测试所选模型”为准。

模型发现与调用格式参考 [OpenAI Docs 模型列表](https://developers.openai.com/api/reference/resources/models/methods/list)
和 [Chat Completions](https://developers.openai.com/api/reference/resources/chat)。

### 其他数据与服务

- 资讯：默认内置公开来源。在设置页按领域/行业勾选，点「保存选择并启用」即可。
  默认每 15 分钟自动采集；无须 Key，后台在切换页面或最小化时继续更新。详情见 `news-sources.md`。
- 自定义资讯（高级可选）：`INDUSTRYPULSE_FEED_URL` / `INDUSTRYPULSE_FEED_TOKEN`，结构见 `dashboard-api.md`。
- 搜索：`INDUSTRYPULSE_SEARCH_URL` 接收 POST `{query, domains, limit}`，返回 `{results:[{url,title,snippet}]}`。
- 抽取：`INDUSTRYPULSE_LLM_URL` 接收 POST `{document,schema,instruction}`，返回 `{candidate:EvidenceCandidate}` 或 `{output:"JSON字符串"}`。
- 搜索/抽取认证使用 `INDUSTRYPULSE_RESEARCH_TOKEN`。未配置时仍可用官方 NBS 发现、确定性表格抽取和手工导入。
- FRED/ALFRED：`FRED_API_KEY`，按指定实时版本日期请求并分页，原始返回先归档。
- 海关：导入数组，字段 hs_code（字符串）、hs_version、year、unit（吨/kg）、value；版本独立保留、千克显式折算为吨。
- AKShare 备用数据：导入数组，列 日期/开盘/收盘/最高/最低/成交量；漂移时报告缺列，不能直接当正式行情发布。

外部来源尚未取得的授权、凭据和数据不会被程序假定存在。FRED 的实时版本参数遵循
[官方 observations 文档](https://fred.stlouisfed.org/docs/api/fred/series_observations.html)。
动态因子使用 [Statsmodels DynamicFactorMQ](https://www.statsmodels.org/dev/generated/statsmodels.tsa.statespace.dynamic_factor_mq.DynamicFactorMQ.html)，预测使用过滤状态，不使用测试期平滑结果。

## 研究工具 JSON

数据与任务 → 采集与研究工具 → 校准/领先滞后/CUSUM，导入以下结构之一：

```json
{"kind":"bootstrap","values":[0.1,0.2,0.4,0.2,0.1,0.3],"block":3}
```

- `lead_lag`：values、target 两个等长数组，train_end、test_start；仅接受训练窗早于测试窗。
- `calibration`：model_id、probabilities、labels、calibration_end、prediction_start；独立校准窗至少 30 个样本且两类齐全，生成的校准曲线单独保存，不自动把原模型标为已验证。
- `monitoring`：values、可选 threshold，返回在线 CUSUM 和状态切换事件。
- `margin_proxy`：steel_price、ore_price、coke_price；缺原料价格返回 null，输出仅为价差代理。

最终 holdout 只能封存未来窗口。封存后从训练与选参排除；评估时导入 `{"id":"封存标识"}`，窗口未结束会拒绝提前查看。30 文档审计包来自真实原件，需要独立填写 expected_value 和 reviewer 后导入；未标注不能声称金样本审计通过。

## 常见故障

- 没有预测：先看失败模型、行业序列和模式。STRICT 历史原件不足时结果为空，重建模式可用；不修改证据等级来补覆盖。
- 无最新输入：查看输入所属期和 age_days；公告尚未发布时，旧报告与其原时点仍保留。
- 抓取失败/挑战页：降低抓取频率；原系统会中止并记录，不覆写旧快照。每日任务默认最多翻 3 页，历史回填默认最多 10 页。
- 模型拟合失败：报告逐模型记录原因，M00 基线可独立显示；高级模型至少 60 个有效训练样本。
- 恢复：备份 Parquet 与 Bronze 原件后，恢复为 `data/restored-*.duckdb`，核对 hash、行数。设置数据库环境变量并重启即可使用。
- 手工导入冲突：同 ID 的不同内容会拒绝；修订使用新 vintage 或新配置版本，禁止覆盖旧记录。

## 验证

```powershell
.venv/Scripts/python.exe -m pytest --basetemp=artifacts/pytest-local
.venv/Scripts/ruff.exe check src tests scripts
.venv/Scripts/python.exe -m mypy src
.venv/Scripts/python.exe scripts/export_contracts.py --check
.venv/Scripts/python.exe scripts/benchmark_workbench.py
```

本轮本地 Windows 验证；现有 CI 配置覆盖 Windows/Linux 与 Python 3.12/3.13。没有远端的情况下不能声称云端矩阵已执行。
