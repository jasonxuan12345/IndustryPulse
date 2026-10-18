# G1 S / A 离线实现与验收记录

2026-10-13。所有权：datahub、ai_research、tests/unit/datahub、tests/unit/ai_research、tests/ai。共享合同、目录 CSV 与共享 fixture 未修改。

架构门禁：纯 HTML 表格工具由集成层移至 core/html_tables.py，A html_extractor 保留公开薄封装，S 只引用 core。A collector 接收本地 ArtifactFetcher Protocol、pipeline 接收只读 ParsedLike Protocol，通过依赖注入使用 S 的实现，避免 S/A 直接导入对方业务模块与循环依赖；冻结合同未改动。

## 实现范围

- S-001：导入 source_catalog.csv 的 18 个候选来源；S0–S3 与 lifecycle status 过滤；许可阻断；拒绝状态必须先重新审核；source lifecycle 转换与结构化审计。目录未声明许可，默认 pending_review，不推断官网入口存在即有采集许可。
- S-002：httpx 下载器；许可/主机约束、404直接失败、429/5xx与网络异常的有界退避、速率间隔、原字节哈希归档与 HTTP header。续传需要强 ETag + If-Range，核验 Content-Range 与总字节数；缺少强验证器则重新下载。已有同 hash 文件须复核，exclusive 创建避免覆写。
- S-003–006：确定性 HTML 表格适配器，精确行业/表头定位；支持 rowspan/colspan、多层表头、列重排、转置。工业利润只允许精确 C31 行，金额/同比序列和单位区分；一月拒绝；PMI 各分项独立 ID；社融 monthly/YTD/stock 独立 ID，统计总体显式声明。公告发布日期只取显式元数据或带标签正文，保留日期/分钟/秒精度。
- S-007–008：合法导出 rows 的 RB/HC 具体合约日行情、日期/数值/重复校验；库存按商品、日期与吨单位核验，以期待周集合计算双商品完整覆盖率。source_verified 固定 false，离线覆盖率不代表官方网站历史覆盖。
- S-009：CSV 与单 worksheet XLSX（标准 OOXML，拒绝公式）批量手工导入；核验 artifact hash 与原官网 host，附原出处、原件、抓取和带时区发布时间，保留零与缺失差异。
- A-001–004：可替换 provider protocol、离线 mock、缺 key 人工 JSON 文件模式、版本化 query/域名白名单、搜索 JSONL 归档和 URL 去重/回放、原文 fetch collector。抓取失败形成 FETCH_FAILED 候选，不读取搜索摘要中的数字。
- A-005–006：规则表格抽取；LLM JSON 使用冻结 EvidenceCandidate 严格 schema。核对完整原件 hash/source URL、真实表格 cell 或精确文本字符跨度、原文数字；不合格拒绝。所有 LLM 候选始终 NEEDS_REVIEW，不能自行变成已审核事实。
- A-007–008：独立双证据评级；同期/可信副本必须显式提供判定，不用抓取日冒充发布时间。冲突按 query、所属期、行列与单位识别，候选 journal 跨调用持久保留，候选 ID 不可覆写原件/数值；人工审查决定另存 append-only 日志。无 Silver 发布路径。
- T-005：恶意 script、诱导文本、近似行业标签、非法 numeric cell、结构/列顺序变化的离线负例。

## 可重现验收

使用项目 .venv 的 Python/ruff，仅自有路径运行 ruff format/check、strict mypy 和 pytest。

```powershell
.venv/Scripts/python.exe -m pytest tests/unit/datahub tests/unit/ai_research tests/ai --basetemp=artifacts/pytest-sa-final
.venv/Scripts/ruff.exe check src/industrypulse/datahub src/industrypulse/ai_research tests/unit/datahub tests/unit/ai_research tests/ai
.venv/Scripts/python.exe -m mypy src/industrypulse/datahub src/industrypulse/ai_research tests/unit/datahub tests/unit/ai_research tests/ai
```

本机默认 Windows TEMP 的 pytest 目录拒绝访问，验收显式使用工作区 basetemp。

## 尚未通过的真源接入门槛

本次仅运行合成样本和 httpx MockTransport，没有实时访问官方网站、采集三份跨月真实公告、核验 robots/许可、验证 SHFE 逐期产品覆盖或运行 30 文档金样本。全部目录来源维持 CANDIDATE，不能宣称真源首发历史覆盖已完成。

S-003–006 是离线表格/元数据解析入口，调用者需要给出审核过的官方行业行、完整表头、统计总体/单位和所属期；不支持的字段/布局拒绝，不猜私有 API。PBC 非表格叙述依赖 LLM 候选后人工审核；PDF/多工作表 XLSX 未实现。AI provider 的真实 Brave/Tavily/模型 HTTP 插件属于配置后的集成，不在本次离线实现里假装可用。

存储日志是单进程 JSONL，生产级多进程并发事务和审核发布是后续仓储/发布集成；原件断点文件同 URL 应由调度避免并发抓取。发布证据与同期 vintage 的真实性依赖真实样本审核，这些离线测试不替代源验收。
