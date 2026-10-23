# 数据源目录（Data Source Catalog）

最后更新：2026-10-23。运行时状态见 `/api/dashboard/data-health`（由 `SourceHealthMonitor` 维护）。
状态：ACTIVE（正常）/ DEGRADED（可用但受限）/ FAILED（本次失败，自动重试）/ BLOCKED（环境封锁）/ MANUAL（需人工）。

## 市场行情

| source_id | 提供方 | 性质 | 端点/方法 | 频率 | 用途 | fallback | 许可说明 |
|---|---|---|---|---|---|---|---|
| tencent:quotes | 腾讯财经 | aggregator | `qt.gtimg.cn/q=` 批量 GBK 文本 | 实时快照（缓存 8min） | 指数/ETF/个股报价、PE/PB/市值 | 无（失败明示） | 公开接口，免登录；仅展示性使用 |
| tencent:kline | 腾讯财经 | aggregator | `web.ifzq.gtimg.cn/appstock/app/fqkline/get` 前复权日 K | 12h | ETF/指数/基准历史 | 东财板块 K 线（板块） | 同上 |
| eastmoney:kline | 东方财富 | aggregator | `push2his.eastmoney.com` 板块日 K | 12h | 行业板块指数（90.BKxxxx） | 行业 ETF K 线 | 公开接口；本网络间歇断连，镜像重试 |
| eastmoney:moneyflow | 东方财富 | aggregator | `push2*.eastmoney.com` fflow 板块资金流 | 30min | 板块主力净流入 | 无——UI 标注失败并指向两融/Shibor | 同上 |

## 宏观（provider=eastmoney 原生，datacenter-web.eastmoney.com，JSON）

| source_id | 报表 | 指标 |
|---|---|---|
| eastmoney:macro:RPT_ECONOMY_GDP | RPT_ECONOMY_GDP | GDP 当季总量（季度） |
| eastmoney:macro:RPT_ECONOMY_CPI | RPT_ECONOMY_CPI | CPI 同比 |
| eastmoney:macro:RPT_ECONOMY_PPI | RPT_ECONOMY_PPI | PPI 同比（BASE_SAME） |
| eastmoney:macro:RPT_ECONOMY_PMI | RPT_ECONOMY_PMI | 制造业 PMI |
| eastmoney:macro:RPT_ECONOMY_ASSET_INVEST | RPT_ECONOMY_ASSET_INVEST | 固定资产投资累计 |
| eastmoney:macro:RPT_ECONOMY_INCOME | RPT_ECONOMY_INCOME | 财政收入累计 |
| eastmoney:macro:RPT_ECONOMY_TAX | RPT_ECONOMY_TAX | 税收收入累计 |
| eastmoney:macro:RPT_ECONOMY_FDI | RPT_ECONOMY_FDI | 实际使用外资 |
| eastmoney:macro:RPT_ECONOMY_HOUSE_PRICE | RPT_ECONOMY_HOUSE_PRICE | 70 城新房价格（北京样本） |
| eastmoney:macro:RPT_MUTUAL_DEAL_HISTORY | RPT_MUTUAL_DEAL_HISTORY | 北向资金累计净买入 |

## 宏观（provider=akshare 适配层，可选依赖；社区维护的公开端点封装）

| source_id | akshare 函数 | 指标 | 备注 |
|---|---|---|---|
| akshare:macro:macro_china_money_supply | macro_china_money_supply | M2/M1 同比 | |
| akshare:macro:macro_china_shrzgm | macro_china_shrzgm | 社融增量 | 上游滞后时标 stale |
| akshare:macro:macro_china_lpr | macro_china_lpr | LPR 1Y/5Y | 分页 |
| akshare:macro:macro_china_hgjck | macro_china_hgjck | 进/出口当月同比 | |
| akshare:macro:macro_china_consumer_goods_retail | macro_china_consumer_goods_retail | 社零同比 | |
| akshare:macro:macro_china_gyzjz | macro_china_gyzjz | 工业增加值同比 | |
| akshare:macro:macro_china_society_electricity | macro_china_society_electricity | 全社会用电量同比 | |
| akshare:macro:macro_china_urban_unemployment | macro_china_urban_unemployment | 城镇调查失业率 | |
| akshare:macro:macro_china_shibor_all | macro_china_shibor_all | Shibor O/N、1W | 日度 |
| akshare:macro:bond_zh_us_rate | bond_zh_us_rate | 中/美国债收益率 10Y（可扩 2/5/30Y） | 分页较慢，日级刷新 |
| akshare:macro:macro_china_market_margin_sh | macro_china_market_margin_sh | 沪市两融余额 | 日度 |
| akshare:macro:macro_china_foreign_exchange_gold | macro_china_foreign_exchange_gold | 外储/黄金储备 | |
| akshare:macro:macro_china_rmb | macro_china_rmb | 美元中间价 | DEGRADED：停更 2021-05 |
| akshare:macro:macro_china_xfzxx | macro_china_xfzxx | 消费者信心 | stale |
| akshare:macro:macro_china_enterprise_boom_index | macro_china_enterprise_boom_index | 企业景气 | stale |

## 资讯（既有，保留）

| source_id | 提供方 | 说明 |
|---|---|---|
| news:*（102 通道） | 各发布方 RSS / RSSHub / 搜索 | 见 `docs/news-sources.md`；本轮新增去重合并与来源聚合展示（source_ids） |

## 研究链路（既有，未动）

| 来源 | 说明 |
|---|---|
| NBS 公告页解析（datahub） | 39 序列 / 2142 观测版本；PIT 三模式；本轮未改动 |
| SHFE 期货（RB/HC） | 既有 |
| 新浪 ETF 日线（V 线管道） | 既有，含分红 |

## BLOCKED / MANUAL

| 源 | 状态 | 说明 |
|---|---|---|
| nbs-easyquery（data.stats.gov.cn 结构化查询） | BLOCKED | 本网络 403（WAF UrlACL）；公告页解析不受影响 |
| push2 系（东财行情推送） | 环境间歇封锁 | 镜像重试 + 快照缓存兜底，恢复即自动有数 |
| LME / SOX / 海外半导体销售 | MANUAL | 无免登录稳定公开源；以 A 股板块+ETF+国内宏观覆盖 |
| FRED | MANUAL | 需要 API Key（适配器已存在） |

## 更新策略与缓存

| 数据 | TTL | 持久化 |
|---|---|---|
| 实时报价 | 8 分钟 | — |
| 日 K 历史 | 12 小时 | DuckDB `ui_bar`（data/market.duckdb） |
| 板块资金流 | 30 分钟 | — |
| 宏观指标 | 日级（`industrypulse market-sync --macro`） | DuckDB `macro_point` |
| 市场总览快照 | 8 分钟后台刷新 | `data/market/overview.json`（含配置指纹，变更自动失效） |
| 健康记录 | 实时 | `data/market/health.json` |
