"""C-008 专项：错误码映射完整性 / 统一响应外壳 / OpenAPI 样例（§14）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from industrypulse.contracts import (
    ERROR_HTTP_STATUS,
    ERROR_REMEDIATION,
    ROUTES,
    ApiResponse,
    ErrorCode,
    ErrorPayload,
    ResearchMode,
    sample_error_payload,
    sample_industry_state_payload,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_all_error_codes_have_status_and_remediation() -> None:
    """§14.3：每个错误码都必须有 HTTP 状态与 remediation（前端不猜字符串）。"""
    assert set(ERROR_HTTP_STATUS) == set(ErrorCode) == set(ERROR_REMEDIATION)
    for status in ERROR_HTTP_STATUS.values():
        assert 400 <= status <= 503
    for text in ERROR_REMEDIATION.values():
        assert len(text) >= 8


def test_unified_response_shell_matches_spec() -> None:
    """§14.1 外壳逐字段对齐（值是虚构示例，形状是合同）。"""
    payload = sample_industry_state_payload()
    assert payload.request_id == "req-example"
    assert payload.research_mode == ResearchMode.RECONSTRUCTED_PIT
    assert payload.data_quality.coverage == pytest.approx(0.82)
    assert payload.warnings[0].code == "PARTIAL_VINTAGE"
    dumped = json.loads(payload.model_dump_json())
    for key in (
        "request_id",
        "as_of",
        "research_mode",
        "snapshot_id",
        "data_quality",
        "result",
        "warnings",
    ):
        assert key in dumped


def test_error_payload_build() -> None:
    err = sample_error_payload()
    assert isinstance(err, ErrorPayload)
    assert err.code == ErrorCode.PIT_NOT_ELIGIBLE
    assert err.http_status == 422
    assert err.remediation


def test_routes_cover_first_batch_api() -> None:
    """§14.2：15 条路由一条不少，方法与归属模块齐备。"""
    paths = {r.path for r in ROUTES}
    assert len(ROUTES) == 15
    assert "/healthz" in paths
    assert "/v1/industries/{id}/forecast" in paths
    assert "/v1/research/discover" in paths
    for r in ROUTES:
        assert r.owner_lane and r.result_kind
    # 只读查询为主；唯一 POST 是受限的 AI 检索任务（§14.2 安全注记）
    posts = [r for r in ROUTES if r.method == "POST"]
    assert [r.path for r in posts] == ["/v1/research/discover"]


def test_api_response_generic_roundtrip() -> None:
    payload = sample_industry_state_payload()
    again = ApiResponse[dict[str, object]].model_validate_json(payload.model_dump_json())
    assert again == payload


def test_openapi_sample_file_in_sync() -> None:
    """contracts/openapi 样例与生成器同步（CI 守护，防手改漂移）。"""
    from industrypulse.contracts import export

    problems = export.check(REPO_ROOT)
    assert problems == [], "合同生成物漂移：\n" + "\n".join(problems)
