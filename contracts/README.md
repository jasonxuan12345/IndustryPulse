# contracts/ — 冻结契约层

C-002..C-008 的 JSON Schema / Pydantic 合同在本目录冻结，C-010 发布至 `docs/contracts-v1`。

并行开发边界：各 lane 只允许依赖本目录与 `tests/fixtures`，
禁止从其他 lane 的内部实现取类型或读私有表。契约变更必须新版本号，旧版本只读保留。
