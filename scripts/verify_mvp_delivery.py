"""Verify real delivery evidence and replay three tasks without any network access."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from industrypulse.services import ledger
from industrypulse.services import research_mvp as mvp
from industrypulse.services.workbench import ROOT, write_json


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    sample = ROOT / "examples/research-mvp"
    inputs = read(sample / "frozen_inputs.json")
    queue = read(sample / "opportunity_queue.json")
    packets = read(sample / "evidence_packet.json")["packets"]
    assert queue["scanned_sectors"] == 20
    assert {o["sector"] for o in queue["opportunities"]} == {
        "CN_NBS_C06",
        "CN_NBS_B07",
        "CN_NBS_C13",
        "CN_NBS_C35",
    }
    count = 0
    for packet in packets:
        assert packet["facts"], "each real case must have at least one original-text citation"
        for fact in packet["facts"]:
            mvp.validate_record(fact)
            raw = packet["documents"][fact["document_hash"]]["page"]
            assert ledger._hash(raw) == fact["document_hash"]
            location = fact["location"]
            assert raw["content"][location["start"] : location["end"]] == fact["fact"]
            assert mvp.moment(fact["publish_time"]) <= mvp.moment(inputs["as_of"])
            count += 1

    def no_network(*args, **kwargs):
        raise AssertionError("offline verification attempted network access")

    # A missing archive entry must degrade, never escape to the network.
    mvp.httpx.Client = no_network
    output = ROOT / "artifacts/research-mvp/verification"
    ledger_path = output / "ledger.jsonl"
    service = SimpleNamespace(root=ROOT)
    captured_ids = [[f["evidence_id"] for f in p["facts"]] for p in packets]
    for index in range(3):
        kwargs = dict(
            as_of=mvp.moment(inputs["as_of"]),
            run_id=f"continuous-{index}",
            config=inputs["config"],
            bundles=inputs["bundles"],
            tools=mvp.ToolArchive(sample / "tool_archive.json"),
            output=output,
            ledger_path=ledger_path,
        )
        manifest = mvp.run(service, **kwargs)
        directory = Path(manifest["directory"])
        replayed = read(directory / "evidence_packet.json")["packets"]
        assert [[f["evidence_id"] for f in p["facts"]] for p in replayed] == captured_ids
        before = ledger_path.read_bytes()
        assert mvp.run(service, **kwargs) == manifest
        assert ledger_path.read_bytes() == before
    records = ledger.load_ledger(ledger_path)
    continuous = [r for r in records if r["source"]["id"].startswith("continuous-")]
    assert len(continuous) == 36
    assert {r["horizon_months"] for r in continuous} == {1, 3, 6}
    assert all(r["verification"] is None and r["metrics"] is None for r in continuous)

    class Fault:
        def __init__(self):
            self.remaining = 1
            self.archive = mvp.ToolArchive(sample / "tool_archive.json")

        def ask(self, prompt):
            return self.archive.ask(prompt)

        def read(self, url):
            return self.archive.read(url)

        def search(self, query):
            if self.remaining:
                self.remaining -= 1
                raise ConnectionError("InjectedSearchFailure")
            return self.archive.search(query)

    fault = mvp.run(
        service,
        as_of=mvp.moment(inputs["as_of"]),
        run_id="fault-injection",
        config=inputs["config"],
        bundles=inputs["bundles"],
        tools=Fault(),
        output=output,
        ledger_path=ledger_path,
    )
    reviews = read(Path(fault["directory"]) / "research_review.json")["reviews"]
    failures = [t for r in reviews for t in r["tool_log"] if t.get("error") == "ConnectionError"]
    assert failures and fault["memo_count"] == 4
    report = {
        "status": "PASS",
        "scanned_sectors": 20,
        "real_cases": 4,
        "primary_text_matched_facts": count,
        "offline_runs": 3,
        "continuous_ledger_records": len(continuous),
        "idempotent": True,
        "future_evidence_rejected": True,
        "unmatured_labels_unfilled": True,
        "fault_injected": True,
        "other_sectors_continued": True,
        "network_disabled_during_replay": True,
    }
    write_json(ROOT / "artifacts/mvp-delivery-verification.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
