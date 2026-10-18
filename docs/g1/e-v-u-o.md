# G1 E / V / U / O 与编排验收

2026-10-13（Asia/Shanghai）。冻结合同、共享 fixture、pyproject 和 CI 未修改。

## E-001..006

economy.EconomyEngine 仅依赖 ObservationRepository，查询保留 snapshot_id。
六维指标携带方向、角色、原始值、缺失原因、祖先 ID、来源和可用日。
支持百分比、同比、百分点、PMI 偏离变换；YTD 增量是明确近似，跨年或
单位/系列不同拒绝。稳健 median/MAD 只在显式训练截止之前拟合，未来训练
参考不能评分历史时点。unknown 不评分，缺失维度为 null 与 coverage。
输出水平、三期月度斜率、改善广度和证据驱动项。多输入配方未登记时返回
缺失，不能混合不同量纲的原始值。

## V-001..003

HistoricalUniverse 在具体时点过滤已上市、未退市的 ETF 类型，排除指数。
MarketDataset 验证行情所属交易日、上市期、停牌和成交量；现金分红使用
未复权收盘价计算，重复分红拒绝，不能与复权价格重复计息。
economic_signal 仅消费已生成 Prediction，拒绝未来、过期及未经验证预测；
信号形状没有市场价格或未来标签。

V-001 真实主表依赖 G2 S-011，本次用合成资产先验证工程边界。
不包含 G2 执行、费用、组合约束及收益有效性结论。

## U-001..006

入口 apps/dashboard/app.py，宏观、雷达、钢铁详情与模型比较四页。
SDK 验证冻结 ROUTES/OpenAPI，支持 httpx 或离线 fixture；模式、时点、
快照、DEMO 和非 PIT 水印始终展示。缺失独立呈现、来源可下钻，少于三个
可评分维度不强绘雷达。模型页展示共同 OOS、Brier 改善、校准、分年误差
和失败状态，未验证模型不推荐。Mock 模型为空，不伪造准确率。
Streamlit AppTest 验证四页和模式切换无需后端即可运行。

启动：`.venv/Scripts/python.exe -m streamlit run apps/dashboard/app.py`

## O-001..005

Settings 从环境读取，密钥 SecretStr 不序列化、不进入诊断或异常文本。
JobRunner 持久 SQLite 幂等 claim，重复成功/在途任务跳过；指数重试有界，
追踪 ID 和状态事件保留。只记录异常类型，失败同输入可重试；中断 worker
需确认已经退出再显式 recover_interrupted。健康监控保留成功时点、窗口、
年龄和失败状态，未知窗口显式标记。

月度任务接受 feature calendar 决策时点及输入 snapshot_id；未发布/生产失败
保留旧指标并显示年龄，模型失败标记 FAILED，不冻结为成功后阻断重试。
预测按哈希归档所有版本，prediction_id 不可改；跨进程加锁、活动文件原子
替换，旧时点不能覆盖新预测。本轮提供批任务入口，未安装系统定时服务。

## C/API 与 services

PipelineService 用公开入口组合 economy、forecast 和 research。
api.create_app 登记 15 条冻结路由，业务 handler 注入；未绑定端点返回
结构化 SOURCE_UNAVAILABLE，discover 默认关闭。
合成集成覆盖原件 → DuckDB snapshot → 六维景气 → API，以及五模型 →
共同 OOS → 不可回改 manifest/report。
强类型 EconomyReport 等在 lane 自有模块，未擅自改变冻结 ApiResponse；
共享报告 schema 发布仍须独立合同版本评审。
