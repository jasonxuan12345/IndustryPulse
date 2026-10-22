# 行业情报工作台

## 启动

在项目根目录执行 `powershell -ExecutionPolicy Bypass -File scripts/start-dashboard.ps1`，
打开 http://127.0.0.1:8765 。旧 Streamlit 页面保留，新界面为默认 HTTP 首页。
使用当前 Python 虚拟环境，无 Node 或前端构建步骤。

## 资讯接入

默认五个领域：半导体、人工智能、新能源、钢铁、医药生物。领域列表由数据驱动，可直接增加。
当前资讯与市场卡片全部为虚构演示。本地研究页单独读取 `data/g2.duckdb`，是实际数据。

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
刷新按钮重新拉取；不进行后台自动轮询。

## 字段

- 根：`mode`, `updated_at`, `source`, `sectors`, `stories`, `companies`。
- 可选根字段：`flow_unit`, `trend_unit`, `trend_label`, `benchmark_label`。
- 领域：`id`, `name`, `change`（当日百分比）, `net_flow`（当日净流入）, `heat`（0–100）, `trend`。
- 趋势点：`date`（YYYY-MM-DD）, `value`, `benchmark`（可空）；各序列应采用一致可比口径。
- 资讯：`id`（稳定唯一）, `sector`（领域 id）, `category`（news/hotspot/personnel）, `title`, `summary`, `published_at`（ISO 8601 含时区）, `source`, `url`（可空）, `company`（可空）, `tags`。
- 公司：`id`, `sector`, `name`, `symbol`, `focus`, `change`（可空）。公司资讯通过 `company` 名称关联。

接口：`GET /api/dashboard/feed`、`POST /api/dashboard/feed`、`GET /api/dashboard/schema`、
`GET /api/dashboard/research`；可在 `/docs` 查看交互 API 文档。
原 `/v1` 冻结合同保持不变。

可选环境变量：`INDUSTRYPULSE_FEED_FILE` 指定 JSON 路径，
`INDUSTRYPULSE_DASHBOARD_DB` 指定真实研究 DuckDB 路径。

## 使用

点击领域切换所有相关卡片；时间窗口同时过滤趋势和资讯；资金卡是当日快照。
搜索匹配标题、摘要、标签、公司；按 `/` 聚焦搜索。资讯可按热点/新闻/人事筛选，
点击展开摘要与原文链接；收藏保存在本浏览器。研究序列点击查看历史曲线与原始来源。
研究页为当前库版本浏览，不作为 STRICT PIT 结果。

原系统的宏观、景气、预测、研究实验、回测、证据审查与运维也已接入此工作台，
使用说明见 `operations.md`。未新增实际新闻供应商、交易下单接口或预测有效性声明。
