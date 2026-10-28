"""合同工件导出：JSON Schema / OpenAPI 样例 / 示例载荷 / 清单。

C-002"版本化 JSON schema"与 C-008"API mock 响应由 schema 生成"的落地机制：

- 所有工件由本模块从 Pydantic 模型**确定性生成**（同一代码必然产出同一字节）；
- 生成物提交入仓（contracts/schemas、contracts/openapi），供非 Python 消费方
  与跨模块联调使用；
- ``check()`` 比对生成物与仓库内容，任何漂移都会被
  tests/contracts/test_export_sync.py 拦截——改合同而忘导出无法通过 CI。

用法（仓库根目录）::

    python scripts/export_contracts.py          # 写出/刷新生成物
    python scripts/export_contracts.py --check  # CI 守护：漂移即退出码 1
"""

from __future__ import annotations

import enum
import hashlib
import inspect
import json
from pathlib import Path
from typing import Any

from . import (
    CONTRACTS_VERSION,
    ERROR_HTTP_STATUS,
    ROUTES,
)
from .common import ContractBaseModel
from .http import sample_error_payload, sample_industry_state_payload

#: 合同模块清单（导出顺序即清单顺序，保持稳定）。
_CONTRACT_MODULES = (
    "common",
    "sources",
    "observation",
    "context",
    "pit",
    "domain",
    "model",
    "research",
    "http",
)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _dump_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"


def _iter_contract_namespaces() -> list[tuple[str, Any]]:
    import industrypulse.contracts as pkg

    return [(m, getattr(pkg, m)) for m in _CONTRACT_MODULES]


def collect_schemas() -> dict[str, str]:
    """每个合同模型/枚举一个独立 JSON Schema 文件（自包含 $defs）。"""
    out: dict[str, str] = {}
    for module_name, ns in _iter_contract_namespaces():
        for attr_name in dir(ns):
            obj = getattr(ns, attr_name)
            if obj is ContractBaseModel or not isinstance(obj, type):
                continue
            if issubclass(obj, ContractBaseModel) and obj is not ContractBaseModel:
                schema = obj.model_json_schema()
            elif (
                isinstance(obj, type)
                and issubclass(obj, enum.Enum)
                and obj.__module__.startswith("industrypulse.contracts.")
            ):
                schema = {
                    "title": obj.__name__,
                    "type": "string",
                    "enum": [member.value for member in obj],
                    "description": inspect.cleandoc(obj.__doc__ or ""),
                }
            else:
                continue
            schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
            schema["$id"] = (
                f"https://industrypulse.local/contracts/{CONTRACTS_VERSION}/{module_name}.{attr_name}.schema.json"
            )
            schema["x-contracts-version"] = CONTRACTS_VERSION
            out[f"{module_name}.{attr_name}.schema.json"] = _dump_json(schema)
    return out


def collect_openapi() -> dict[str, str]:
    """OpenAPI 样例（G0 为路由骨架；真实 spec 由 G1 FastAPI 生成）。"""
    paths: dict[str, Any] = {}
    for route in ROUTES:
        entry = paths.setdefault(
            route.path,
            {
                "summary": route.result_kind,
                "x-owner-lane": route.owner_lane,
            },
        )
        entry[route.method.lower()] = {
            "operationId": route.path.strip("/")
            .replace("/", "_")
            .replace("{", "")
            .replace("}", ""),
            "parameters": [{"name": p, "in": "query"} for p in route.params],
            "responses": {"200": {"description": "ApiResponse 外壳，见 examples/"}},
        }
    spec = {
        "openapi": "3.1.0",
        "info": {
            "title": "IndustryPulse API（G0 契约样例，非可运行服务）",
            "version": CONTRACTS_VERSION,
            "description": (
                "由 industrypulse.contracts 导出的路由骨架与错误码清单。"
                "统一响应体外壳见 contracts/openapi/examples/industry_state.json；"
                "真实 OpenAPI 由 G1 的 FastAPI 服务自动生成并与此清单对齐。"
            ),
        },
        "paths": paths,
        "x-error-codes": {
            code.value: {"http_status": status} for code, status in ERROR_HTTP_STATUS.items()
        },
    }
    files = {"openapi.json": _dump_json(spec)}
    files["examples/industry_state.json"] = (
        sample_industry_state_payload().model_dump_json(indent=2) + "\n"
    )
    files["examples/error_payload.json"] = sample_error_payload().model_dump_json(indent=2) + "\n"
    return files


def build_artifacts() -> dict[str, str]:
    """全部生成物：相对路径 → 文本内容。"""
    artifacts: dict[str, str] = {}
    for name, content in collect_schemas().items():
        artifacts[f"schemas/{CONTRACTS_VERSION}/{name}"] = content
    for name, content in collect_openapi().items():
        artifacts[f"openapi/{name}"] = content
    return artifacts


def build_manifest(artifacts: dict[str, str]) -> str:
    """生成物清单：文件 → sha256（contracts-v1 锁定的机器可读指纹）。"""
    manifest = {
        "contracts_version": CONTRACTS_VERSION,
        "files": {
            path: _sha256_bytes(content.encode("utf-8"))
            for path, content in sorted(artifacts.items())
        },
    }
    return _dump_json(manifest)


def write_all(root: Path) -> list[Path]:
    """写出全部生成物到 root（仓库的 contracts/ 目录），返回写入文件清单。"""
    artifacts = build_artifacts()
    written: list[Path] = []
    base = root / "contracts"
    for rel, content in artifacts.items():
        target = base / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
        written.append(target)
    manifest_path = base / "MANIFEST.json"
    manifest_path.write_text(build_manifest(artifacts), encoding="utf-8", newline="\n")
    written.append(manifest_path)
    return written


def check(root: Path) -> list[str]:
    """比对生成物与仓库内容；返回漂移清单（空列表 = 同步）。"""
    artifacts = build_artifacts()
    expected = dict(artifacts)
    expected["MANIFEST.json"] = build_manifest(artifacts)
    base = root / "contracts"
    problems: list[str] = []
    for rel, content in expected.items():
        target = base / rel
        if not target.exists():
            problems.append(f"缺失：contracts/{rel}")
        elif target.read_text(encoding="utf-8") != content:
            problems.append(f"内容漂移：contracts/{rel}（运行 scripts/export_contracts.py 刷新）")
    # 反向检查： schemas/openapi 目录下存在但不在生成清单里的文件（防手工篡改）
    generated_dirs = [base / "schemas", base / "openapi"]
    expected_paths = {base / rel for rel in expected}
    for gen_dir in generated_dirs:
        if not gen_dir.exists():
            continue
        for file in gen_dir.rglob("*"):
            if file.is_file() and file not in expected_paths:
                problems.append(f"多余文件：{file.relative_to(base)}（非生成物，须移除）")
    return problems


__all__ = [
    "build_artifacts",
    "build_manifest",
    "check",
    "collect_openapi",
    "collect_schemas",
    "write_all",
]
