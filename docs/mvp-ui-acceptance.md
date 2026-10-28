# 研究前端与空状态验收

时间：2026-10-28，北京时间。

本次补齐用户提出的三项内容：新研究归档接入前端、缺数据原因及操作入口、移除纯占位空卡片。

## 已交付

- `/research`：独立的 AI 基本面研究页面。归档选择、行业/资产搜索、建议状态筛选、证据展开、估值情景与退出条件、简报和 JSON 下载均接到真实接口。
- `/research/quant`：保留原有量化研究页面和操作，避免两条研究轨道混淆。
- `/api/research/mvp/report`：核验冻结 JSON 的hash后提供精简投影，不把巨量原始行情与正文塞进列表接口。损坏归档返回明确错误；无结果与未运行有独立状态。
- `/api/research/mvp/run`：通过既有工作台队列运行，202立即返回，页面轮询任务结果。重复提交复用当前任务；新运行另建归档。
- 总览、趋势、公司观察：有数据时继续展示；资金流/走势/商品/基准完全缺失时移除空图卡片，缺项集中展示更新时间、原因与刷新/设置入口。部分来源缺失时只绘制可用项，不把null作为0；真实0正常保留。
- 行情服务初始化失败（包括数据库被其他进程占用）不会拖垮资讯接口，保留资讯并返回可解释的行情失败状态。

## 验证结果

```text
python -m pytest --disable-warnings --basetemp=artifacts/ui-pytest-final
623 passed, 1 skipped, 54 warnings in 45.20s
```

原始输出：`artifacts/ui-final-tests.txt`。新增4项 Python 回归覆盖空归档、精简字段、hash损坏、路径限制、下载、异步防重复提交，以及行情初始化失败时资讯仍可用。

```text
node scripts/check_dashboard_states.cjs
PASS: empty panels omitted; zero values preserved; partial sources filtered; recovery actions present.
```

另外对 `app.js`、`dashboard-states.js`、`research-mvp.js` 执行 `node --check`；新增 Python 服务和路由的 ruff、mypy 检查通过。项目原有静态检查遗留项见原 MVP 验收报告，本轮未将其冒充全部修复。

浏览器在真实本地服务上验证：

1. 读取已有归档，显示20行业、四条建议、七条正文摘录。
2. 搜索“油气”定位石油和天然气开采业；无匹配显示原因和清除筛选按钮，清除后恢复列表。
3. 切换 `delivery-offline` / `delivery-cases` 归档，保持每轮的原始研究时间与结果。
4. 展开原始引用，查看发布/获取时间、摘录位置及官方来源链接。
5. 总览缺失行情时显示数据状态提示，空走势图和资金流卡片消失；已缓存资讯、宏观数据正常显示。
6. 原服务重启后在8765地址返回新版接口200，服务仍只监听127.0.0.1。原自动研究配置保留。验收用8767服务已停止。

截图：`artifacts/ui/research-page.png`、`artifacts/ui/overview-page.png`。本次做了真实桌面浏览器验收；响应式断点已实现，未将其描述为完整移动设备矩阵测试。

## 数据边界

本次没有把未知估值、目标价或预期收益补成数字。现有研究仍为 WATCH，重建证据与待核验关系在界面明确显示。缺项的展示问题已经处理，外部数据源是否返回真实可用数据仍以接口实际结果为准。
