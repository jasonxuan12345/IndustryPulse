import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from industrypulse.ai_research.classifier import classify
from industrypulse.ai_research.collector import collect
from industrypulse.ai_research.html_extractor import locate
from industrypulse.ai_research.llm_extract import validate_output
from industrypulse.ai_research.pipeline import table_candidate
from industrypulse.ai_research.planner import allowed_url, plan
from industrypulse.ai_research.provider import ManualFileProvider, SearchResult, select_provider
from industrypulse.ai_research.review_queue import ReviewQueue
from industrypulse.ai_research.searchlog import archive_search, replay
from industrypulse.contracts.observation import EvidenceCandidate, EvidenceGrade, RawArtifact
from industrypulse.contracts.sources import LegalStatus, TimestampPrecision
from industrypulse.datahub.adapters import parse_profit
from industrypulse.datahub.fetcher import Fetcher
from industrypulse.datahub.source_registry import SourceRegistry


def candidates() -> list[EvidenceCandidate]:
    data = json.loads(
        Path("tests/fixtures/g0/evidence_candidates.json").read_text(encoding="utf-8")
    )
    return [EvidenceCandidate.model_validate(row) for row in data["candidates"]]


def test_search_replay_and_manual(tmp_path: Path) -> None:
    query, text = plan("cn.nbs.profit", "2025-03", "国家统计局", "stats.gov.cn", datetime.now(UTC))
    assert all(token in text for token in ("2025", "3", "cn.nbs.profit", "国家统计局"))
    results = [
        SearchResult("https://www.stats.gov.cn/doc#part", "公告"),
        SearchResult("https://www.stats.gov.cn/doc", "重复"),
        SearchResult("https://stats.gov.cn.evil.example/doc", "伪装"),
    ]
    archive = tmp_path / "search.jsonl"
    assert len(archive_search(archive, query, text, datetime.now(UTC), results)) == 1
    assert replay(archive)[0]["search_text"] == text
    manual = tmp_path / "manual.json"
    manual.write_text('{"results": [{"url":"https://www.stats.gov.cn/doc","title":"manual"}]}')
    provider = select_provider(None, manual)
    assert isinstance(provider, ManualFileProvider)
    assert provider.search(query, text)[0].title == "manual"
    assert not allowed_url("https://user@stats.gov.cn/doc", query.allowed_domains)


def test_collector_failed_snippet(tmp_path: Path) -> None:
    query, _ = plan("cn.nbs.profit", "2025-03", "国家统计局", "stats.gov.cn", datetime.now(UTC))
    sources = SourceRegistry.from_csv(Path("source_catalog.csv"))
    sources.set_permission("src.nbs.profit", LegalStatus.PUBLIC_OFFICIAL, reason="synthetic")
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(404))) as client:
        fetcher = Fetcher(sources, tmp_path, client)
        result = collect(
            fetcher,
            "src.nbs.profit",
            query,
            SearchResult("https://www.stats.gov.cn/doc", "公告", "profit 999"),
            "国家统计局",
        )
    assert isinstance(result, EvidenceCandidate)
    assert result.parsed_value is None and result.raw_sha256 is None


@pytest.mark.parametrize(
    "precision,expected",
    [
        (TimestampPrecision.SECOND, EvidenceGrade.A),
        (TimestampPrecision.DAY, EvidenceGrade.B),
        (TimestampPrecision.MONTH, EvidenceGrade.C),
    ],
)
def test_grades(precision: TimestampPrecision, expected: EvidenceGrade) -> None:
    verified, failed, mirror = candidates()
    assert classify(verified, precision=precision, contemporary=True) == (expected, EvidenceGrade.A)
    assert (
        classify(mirror, precision=precision, contemporary=True, trusted_copy=True)[1]
        == EvidenceGrade.B
    )
    assert classify(failed, precision=precision) == (EvidenceGrade.D, EvidenceGrade.D)
    assert classify(verified, precision=precision)[1] == EvidenceGrade.C


def test_conflicts_preserve_originals(tmp_path: Path) -> None:
    verified, _, mirror = candidates()
    queue = ReviewQueue(tmp_path / "queue.jsonl")
    result = queue.reconcile([verified, mirror])
    assert all(item.verification_status == "needs_review" for item in result)
    assert verified.verification_status == "verified"
    assert {item.parsed_value for item in result} == {verified.parsed_value, mirror.parsed_value}
    assert len(queue.path.read_text(encoding="utf-8").splitlines()) == 2
    queue.record_decision(
        verified.candidate_id, "reviewer", True, "original checked", datetime.now(UTC)
    )
    assert queue.path.with_suffix(".decisions.jsonl").exists()


def test_llm_evidence_binding(tmp_path: Path) -> None:
    candidate = candidates()[0]
    raw = candidate.raw_text.encode() if candidate.raw_text else b""
    digest = hashlib.sha256(raw).hexdigest()
    artifact = RawArtifact(
        artifact_id=digest,
        source_id="src.nbs.profit",
        uri=str(tmp_path),
        sha256=digest,
        mime="text/html",
        byte_size=len(raw),
        retrieved_at=datetime.now(UTC),
        source_url=candidate.source_url,
    )
    payload = {
        **candidate.model_dump(mode="json"),
        "raw_sha256": digest,
        "table_locator": f"text[0:{len(raw.decode())}]",
    }
    valid = validate_output(json.dumps(payload), raw, artifact)
    assert valid.verification_status == "needs_review"
    payload["parsed_value"] = "999999"
    with pytest.raises(ValueError):
        validate_output(json.dumps(payload), raw, artifact)
    with pytest.raises(ValueError):
        validate_output("not-json", raw, artifact)


def test_exact_locator_ambiguity() -> None:
    html = "<table><tr><th>行业</th><th>利润</th></tr><tr><td>钢铁</td><td>1</td></tr></table>"
    with pytest.raises(ValueError):
        locate(html + html, "钢铁", "利润")
    assert locate(html + html, "钢铁", "利润", table_index=2).text == "1"


def test_conflict_across_separate_calls(tmp_path: Path) -> None:
    original, _, mirror = candidates()
    queue = ReviewQueue(tmp_path / "queue.jsonl")
    queue.reconcile([original])
    result = queue.reconcile([mirror])
    assert result[0].verification_status == "needs_review"
    assert original.candidate_id in queue.path.read_text(encoding="utf-8")


def test_candidate_bound_to_archive_and_series(tmp_path: Path) -> None:
    from dataclasses import replace

    html = "<table><tr><th>行业</th><th>利润总额 / 金额</th></tr>"
    html += "<tr><td>黑色金属冶炼和压延加工业</td><td>5</td></tr></table>"
    raw = html.encode()
    path = tmp_path / "raw.html"
    path.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    artifact = RawArtifact(
        artifact_id=digest,
        source_id="src.nbs.profit",
        uri=str(path),
        sha256=digest,
        mime="text/html",
        byte_size=len(raw),
        retrieved_at=datetime.now(UTC),
        source_url="https://www.stats.gov.cn/doc",
    )
    parsed = parse_profit(
        html, period="2025-03", row="黑色金属冶炼和压延加工业", column="利润总额 / 金额"
    )
    query, _ = plan(
        parsed.series_id, parsed.period, "国家统计局", "stats.gov.cn", datetime.now(UTC)
    )
    assert (
        table_candidate(query, parsed, artifact, publisher="国家统计局", title="公告").parsed_value
        == 5
    )
    with pytest.raises(ValueError):
        table_candidate(
            query,
            replace(parsed, series_id="cn.nbs.wrong"),
            artifact,
            publisher="国家统计局",
            title="公告",
        )
    with pytest.raises(ValueError):
        table_candidate(
            query, replace(parsed, raw_text="999"), artifact, publisher="国家统计局", title="公告"
        )
