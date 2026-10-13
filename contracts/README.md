# contracts/ — 冻结契约层（contracts-v1）

本目录存放**生成物**（JSON Schema / OpenAPI 样例 / 示例载荷 / 清单），全部由
`src/industrypulse/contracts/` 的 Pydantic 模型经 `python scripts/export_contracts.py`
确定性导出。**禁止手改**；`tests/contracts/test_http.py::test_openapi_sample_file_in_sync`
与 CI 守护漂移。

```text
contracts/
  schemas/1.0.0/     每个合同模型/枚举一个自包含 JSON Schema（版本化目录）
  openapi/
    openapi.json     §14.2 路由骨架样例（真实 spec 由 G1 FastAPI 生成并与之对齐）
    examples/        由 schema 生成的 API mock 响应（C-008 验收）
  MANIFEST.json      全部生成物的 sha256 清单（contracts-v1 锁定指纹）
```

- 可执行真值是 Python 包 `industrypulse.contracts`（版本常量 `CONTRACTS_VERSION`）；
- 契约变更流程：改模型 → 递增 `CONTRACTS_VERSION` → 重跑导出脚本 → contracts PR（§3.3）；
- 并行开发边界：各 lane 只准依赖 `industrypulse.contracts` 与 `tests/fixtures`，
  禁止从其他 lane 的内部实现取类型或读私有表。

版本发布说明与模块路径所有权表见 `docs/contracts-v1/`。
