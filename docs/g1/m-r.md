# G1 M / R 核心研究闭环

实现 M-001..009、R-001..008 和 T-004。入口为 `features`、`forecast`、`research`。
所有验证仅使用合成数据，模型输出始终为 `not_validated`、`uncalibrated`。

- 冻结目标 T1 使用经济所属月份 +3；T2 使用第3个有效新周期公告。
  缺目标不填充，跨年、负利润、非正营收、口径变化显式 UNSCORABLE。
- 25候选、TargetSpec 定义落在 M 自有 `features`，而非任务表中的
  `config/targets`，遵守 contracts-v1 所有权表。共享合同未修改。
- 决策历完全依赖输入交易日历，返回每月最后交易日上海时间17点。
  派生特征递归检查祖先闭包，未来可用祖先和循环依赖拒绝。
- 每折独立拟合中位数、尺度和missing列；变换缓存仅限当前拟合实例。
  M00 延续及基础率、M01 同月均值窗口、M02 因果递归 AR、M03 Logistic、
  M04 elasticnet Logistic。样本不足拒绝，报告保留失败，不静默替代。
- 参数网格只能按预注册更早内层折 Brier 搜索；标签释放必须早于训练时钟。
  purge 使用实际目标期结束及配置月份，另加 embargo。
- manifest 按 experiment_id 与配置哈希独占创建；改变配置必须新 run；
  报告独占写入并逐字重放核验。训练产物哈希含完整配置及训练帧。
- 对照仅比较共同已公布 OOS 样本，报告所有模型、分年、分行业、混淆矩阵、
  可靠性曲线、Brier/log-loss/AUC/PR-AUC；单类 AUC 为 null。
  MAE/RMSE/MASE 的尺度只来自训练序列。
- 3%改善和至少两个年份一致仅作候选筛选；G1 不具备独立校准及依赖性
  置信区间，因此保持 NO_EDGE，不宣称真实预测优势。G2 的区块 bootstrap、
  独立校准、最终holdout 与三模式真实数据比较尚未启用。

验证：`python -m pytest tests/research tests/unit/features tests/unit/forecast`，
14个测试通过，以及自有路径 ruff/mypy。Windows 临时目录权限异常时使用
`--basetemp=artifacts/pytest-mr-<unique-run>`。

服务层调用方式（R仅依赖冻结合同，具体模型由编排层注入）：

```python
from industrypulse.forecast import BaselineModel
from industrypulse.research import run_experiment

report = run_experiment(
    features, targets, frozen_spec, artifact_directory,
    model_factory=lambda model_id, parameters: BaselineModel(model_id, **parameters),
)
```

可运行合成spec构造见 `tests/research/test_g1.py::data`。`created_at`作为报告评价
可见性时钟；`train_cutoff`是所有外折的上限；每折训练日期进一步截断该时钟。
代码、依赖、输入数据与行业映射哈希由服务层按真实inventory提供；合成测试的
占位哈希仅用于验证冻结与重放机制，不宣称真实数据及代码版本已完成认证。
