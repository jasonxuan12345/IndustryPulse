# 跨平台发布核验

日期：2026-10-28。目标为完整源码检出后，在 Windows、macOS、Linux 上配置兼容API并启动浏览器工作台。不能将它理解为支持所有任意原生API或不需要历史数据。

## 本机已执行

- Windows / Python 3.13，全套：`630 passed, 1 skipped, 54 warnings in 46.55s`。
- `ruff check src tests scripts`：全部通过。
- `ruff format --check src tests scripts`：全部通过。
- `mypy src`：`Success: no issues found in 141 source files`。
- 合同生成物检查、Node前端状态校验均通过。
- uv.lock 与 pyproject 一致性检查通过。
- 公开样例在禁用网络后验证四行业、七条原文摘录、连续3次执行、幂等入账和单次搜索故障降级。
- 将源码复制到没有本机数据、密钥或缓存的新目录后，启动页面及研究 API 的冒烟检查通过。

新增用例验证：没有本地模型配置文件时从环境变量加载、空示例环境不误报、兼容 Chat/Responses 两协议、采用用户模型、不同服务不复用密钥、从其他工作目录调用启动器、没有历史数据库时用新闻线索完成20行业扫描并降级观察。

## 自动化平台覆盖

[GitHub Actions](https://github.com/jasonxuan12345/IndustryPulse/actions) 对每次推送验证：

- Ubuntu / Windows / macOS，各运行 Python3.12、3.13，共六组。
- 静态检查、合同、全套回归、前端校验、实际启动通用服务并请求页面/研究API。
- 独立Ubuntu作业构建Docker镜像，运行非root容器并检查研究API和页面。

2026-10-28 已完成实际远程验证：代码提交 `69c19fc2a374247c06f95af6877834416f50ffb6` 的 [Actions 运行 40689393451](https://github.com/jasonxuan12345/IndustryPulse/actions/runs/40689393451) 结果为 **success**，六组系统/Python测试及Docker作业共七个作业全部通过。完成时间为 12:45:39 UTC（北京时间20:45:39）。

Windows 的 UTF-8 控制台、Python 3.12/3.13 的合同导出差异、macOS LightGBM 所需的 OpenMP 运行库，以及容器启动就绪等待，均已在本次修复后通过相应平台验证。CI 使用无真实密钥的测试和服务启动检查；兼容协议由模拟响应验证，不代表实际调用了所有服务商。此处后续仅补充文档，不改变该次已测代码。

## 当前边界

- 支持标准 OpenAI 兼容 Chat Completions / Responses，原生其他鉴权或协议需适配。
- 模型和搜索是两组独立连接，默认 DeepSeek Search/Fetch，可接兼容JSON网关。
- 首装可运行资讯与AI观察链路；历史预测/回测需导入数据，自动日程需有效交易日历。
- 没有估值输入时继续WATCH/REJECT，不生成目标价和收益承诺。
- 单独wheel不包含完整根目录资源；本次支持源码检出安装或完整Docker镜像。
- `.env`、用户密钥、生产数据、缓存、运行日志不入Git；公开样例与截图单独提供。

历史MVP与UI验收的结果、计数和限制保留在原文中；其中关于既有静态检查失败的旧记录已经被本轮修复，不代表当前检查状态。
