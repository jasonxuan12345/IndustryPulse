# 部署与 API 配置

## 支持范围

Windows、macOS、Linux 使用同一个 Python/FastAPI 服务和静态浏览器前端。建议从 GitHub 克隆源码后可编辑安装，Python 为3.12/3.13，默认端口8765。Node 不属于运行依赖。独立窗口的 Windows Job Object 启动器仅适用于 Windows；通用入口是 `python scripts/start_dashboard.py`。

源码中的配置、静态页面和迁移文件通过源码根目录定位。请保留完整检出目录；当前不承诺只有一个 wheel 文件即可脱离这些资源独立安装。Docker 镜像会包含完整源码。

## 配置优先级

跨平台脚本自动读取根目录 `.env`，不会覆盖已经设置的进程环境变量。模型配置优先读取非空 `INDUSTRYPULSE_MODEL_*` 覆盖项，再读取 `data/model-provider.json`。环境变量配置仅用于当前运行，不写回本机配置文件；设置页会保存本机文件。改变 Base URL 不会自动继承其他来源的旧密钥。

模型变量：`INDUSTRYPULSE_MODEL_BASE_URL`、`INDUSTRYPULSE_MODEL_API_KEY`、`INDUSTRYPULSE_MODEL_NAME`、`INDUSTRYPULSE_MODEL_PROTOCOL`。协议是 `chat_completions`（默认）或 `responses`；模型名称使用实际服务商列表中的ID。独立 CLI 使用进程环境变量或保存的设置；自动读取 `.env` 是启动脚本的功能，可通过 `uv run --env-file .env industrypulse research-mvp` 为 CLI 显式加载。

服务商兼容性：标准 Chat Completions 的 `/chat/completions`、模型列表 `/models`；或 Responses 的 `/responses`。无法列出模型时可在设置页手动输入。没有强制给其他供应商发送 DeepSeek 的 `thinking` 参数。模型返回的 JSON 对象可包含 Markdown 代码围栏，解析后仍需经过事实校验。

## 搜索与正文接口

`INDUSTRYPULSE_SEARCH_URL` / `INDUSTRYPULSE_FETCH_URL` 覆盖 `config/research/mvp.yaml`，`INDUSTRYPULSE_SEARCH_KEY` 是独立的 Bearer 凭据。同来源（协议、主机、端口均相同）的服务可使用模型密钥；其他来源必须另配检索凭据。匿名自建服务可不配置密钥。默认 DeepSeek 检索未配置有效凭据时显式降级。

检索请求：`POST {query, count, need_content}`，响应：`{results: [{url, title, date}]}`，`date` 为带时区的发布时间，未知则null。页面请求：`POST {url}`，响应：`{title, content}`，content是正文文本或Markdown，不能用AI改写稿冒充。其他API（包括 Tavily 原生响应等）需通过适配层转换成这套形状，不能只改URL就假定兼容。

模型设置不决定搜索供应商，二者凭据分别管理。缺搜索、日期或原始正文时仍生成有缺口的观察记录，不伪称完成核实。

## 新环境与数据

空 `data/` 可以启动前端、配置模型并抓取公开资讯。首次研究仍扫描20行业；未导入历史统计/行情时使用本地已缓存新闻线索，明确标出输入缺口，无触发则保存空队列，不凑数。历史量化模型与回测需要导入的数据包、交易日历、分红与原件证据，不能仅凭模型 API Key 获得这些资料。

`examples/research-mvp/` 用公开披露样例证明离线链路可复现；历史输入已裁剪到校验所需的近期窗口，保留重建证据标记。执行 `python scripts/verify_mvp_delivery.py` 不需要API，它会禁止网络请求并写到 `artifacts/`。

## 容器与服务器

`docker compose up --build -d` 使用根目录 `.env`，默认端口仅发布到127.0.0.1。`data/`、`artifacts/` 使用命名卷，容器以普通用户运行。停止使用 `docker compose down`，数据卷不会因此删除。导入数据包应使用页面或 `docker compose cp`，不要把个人 `.env` 或数据库加入镜像。

容器连接宿主模型：Docker Desktop 可使用 `host.docker.internal`；Linux Docker Engine 需按自身网络配置添加宿主网关。模型服务需实际监听可达地址，不能把容器内部 localhost 当作宿主。远程访问可通过SSH端口转发；应用本身没有多用户鉴权，不直接暴露到公网。

本机尚无 Docker 运行时，因此 Docker 文件属于提供的部署方案；实际镜像构建及跨系统结果记录在发布核验中，不将未执行的构建写成通过。

## 排查

- 400/404：核对模型ID、基础URL与协议；不保证任何原生非兼容供应商都能直接使用。
- 401/403/429：检查对应模型或检索服务的权限、额度与限流，日志仅记录必要错误，不发布凭据。
- 公开源失败：网络、地区限制、来源改版可能造成缺项；保留上次成功缓存。代理可按 httpx 的 `HTTP_PROXY/HTTPS_PROXY/NO_PROXY` 配置，localhost应包含在NO_PROXY中。
- 数据库占用：DuckDB使用单写进程，避免多个应用同时写同一数据目录；行情初始化失败会保留资讯页面，修复后重启。
- 未生成研究：等待首次资讯采集，检查订阅、统计数据与搜索配置。没有证据时不会强行生成建议。
- 日历未知：自动研究跳过并告警，需要更新交易日历；手动运行仍可执行只读研究。
- 停止：命令行Ctrl+C；Windows独立窗口使用原启动器的关闭机制；容器使用Compose停止命令。
