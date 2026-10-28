# 行业情报工作台

## 启动

在项目根目录执行 `python scripts/start_dashboard.py`（三大桌面系统通用），
打开 http://127.0.0.1:8765 。旧 Streamlit 页面保留，新界面为默认 HTTP 首页。
使用当前 Python 虚拟环境，无 Node 或前端构建步骤。

## 资讯接入

默认内置 102 个公开订阅通道、12 个领域、30 个细分行业。启动即读取真实资讯，
界面「数据接入与设置」可按行业勾选来源，保存后立即采集并持久保存，默认每 15 分钟更新。
无需模型 Key，也不需要用户寻找资讯 API。数量含栏目和主题搜索，不等于 102 家独立媒体。
行情、资金流与价格趋势未接入时保持空值；不会用新闻或模型生成这些数值。
本地研究页单独读取 `data/g2.duckdb`。完整说明见 [资讯源](news-sources.md)。

以下 JSON / 远程统一接口是可选的高级接入方式，不是日常使用的前置条件：

1. 页面「数据接入与设置」导出 JSON，得到完整模板。
2. 将供应商数据映射到模板，将 `mode` 设为 `live`；缺失值用 `null`，无新闻用 `[]`。
3. 可直接在页面导入 JSON（持久保存到 `data/intelligence.json`），或提供远程接口：

```powershell
$env:INDUSTRYPULSE_FEED_URL = 'https://example.com/feed'
$env:INDUSTRYPULSE_FEED_TOKEN = 'optional-bearer-token'
./scripts/start-dashboard.ps1
```

远程接口返回与导出文件相同结构。远程优先；令牌只在服务端发送。
失败时显示错误并保留浏览器上次成功数据，不自动降级成演示。
内置资讯模式在后台按配置自动更新，刷新按钮立即采集。

## 字段

- 根：`mode`, `updated_at`, `source`, `sectors`, `stories`, `companies`。
- 可选根字段：`flow_unit`, `trend_unit`, `trend_label`, `benchmark_label`。
- 领域：`id`, `name`, `change`（当日百分比）, `net_flow`（当日净流入）, `heat`（0–100）, `trend`。
- 趋势点：`date`（YYYY-MM-DD）, `value`, `benchmark`（可空）；各序列应采用一致可比口径。
- 资讯：`id`（稳定唯一）, `sector`（领域 id）, `category`（news/hotspot/personnel）, `title`, `summary`, `published_at`（ISO 8601 含时区）, `source`, `url`（可空）, `company`（可空）, `tags`。
- 公司：`id`, `sector`, `name`, `symbol`, `focus`, `change`（可空）。公司资讯通过 `company` 名称关联。

接口：`GET /api/dashboard/feed`（已并入真实行情：sectors[].change/trend/net_flow/heat、
companies[].quote、market 元数据）、`POST /api/dashboard/feed`、`GET /api/dashboard/schema`、
`GET /api/dashboard/research`；可在 `/docs` 查看交互 API 文档。
行情/宏观（2026-10-23 起）：`GET /api/dashboard/market/overview`（可选 `?refresh=1` 同步重建）、
`GET /api/dashboard/sectors/{id}`、`GET /api/dashboard/macro`、`GET /api/dashboard/data-health`。
内置源：`GET /api/dashboard/sources`、`POST /api/dashboard/sources/settings`、
`POST /api/dashboard/sources/refresh`。刷新立即返回采集进度，不阻塞界面。
原 `/v1` 冻结合同保持不变。
行情层可用 `INDUSTRYPULSE_MARKET_DATA=0` 整体停用；离线刷新用
`industrypulse market-sync --history --macro`。来源与 fallback 见 `data/data-source-catalog.md`。

可选环境变量：`INDUSTRYPULSE_FEED_FILE` 指定 JSON 路径，
`INDUSTRYPULSE_DASHBOARD_DB` 指定真实研究 DuckDB 路径。

## 使用

点击领域切换所有相关卡片；时间窗口同时过滤趋势和资讯；资金卡是当日快照。
搜索匹配标题、摘要、标签、公司；按 `/` 聚焦搜索。资讯可按热点/新闻/人事筛选，
点击展开摘要与原文链接；收藏保存在本浏览器。研究序列点击查看历史曲线与原始来源。
研究页为当前库版本浏览，不作为 STRICT PIT 结果。

原系统的宏观、景气、预测、研究实验、回测、证据审查与运维也已接入此工作台，
使用说明见 `operations.md`。公开订阅不提供付费全文权限；不包含交易下单功能。


## 跨行业 AI 研究接口

- `GET /research`：AI基本面门户；原量化页面是 `/research/quant`。
- `GET /api/research/mvp/report?run_id=...`：精简归档投影，无归档为 `not_run`；损坏hash为409；未知运行为404。不输出密钥或巨量原始行情。
- `POST /api/research/mvp/run`：以202返回既有任务队列ID；重复点击复用正在排队/运行的任务。
- `GET /api/workbench/jobs/{id}`：后台任务状态与已保存结果。
- `GET /api/research/mvp/download/{run_id}/{name}`：下载白名单内的队列、证据、审查、memo或简报。

前端会区分未运行、无触发、筛选无匹配、部分补证失败、归档损坏和请求失败。缺行情时隐藏纯空图卡片，保留其他有效资讯与恢复入口；不能把null当成真实零值。

模型配置支持保存到本机或通过环境变量提供。进程环境变量与 `.env` 的说明见 [部署文档](deployment.md)。模型和检索的凭据来源分别校验。
