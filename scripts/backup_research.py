"""Create and verify a local research backup without copying provider credentials."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess

from industrypulse.services import ledger
from industrypulse.services.workbench import Workbench, write_json


def main() -> None:
    service = Workbench()
    root = service.root
    book = root / "data/ledger/recommendations.jsonl"
    with (
        ledger._lock(root / "data/research-loop/runtime"),
        ledger._lock(root / "data/ledger/runtime"),
        ledger._lock(book),
    ):
        original = ledger._read(book)
        report = service.backup({})
        target = service.output / "backups" / report["backup"]
        for name in ("ledger", "research-loop", "research", "leading", "baseline", "bronze-etf"):
            source = root / "data" / name
            if source.exists():
                shutil.copytree(
                    source,
                    target / "research-data" / name,
                    ignore=shutil.ignore_patterns("*.lease.sqlite3*"),
                )
        news = root / "data/news/cache.json"
        if news.exists():
            destination = target / "research-data/news/cache.json"
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(news, destination)
        copied = ledger._read(target / "research-data/ledger/recommendations.jsonl")
        if copied != original:
            raise ValueError("Copied ledger differs from locked source")
        hashes = {
            p.relative_to(target).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in target.rglob("*")
            if p.is_file()
        }
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
        ).stdout.strip()
        write_json(
            target / "research-manifest.json",
            {
                "code_revision": revision,
                "record_count": len(original),
                "ledger_head": original[-1]["record_hash"] if original else None,
                "sha256": hashes,
                "scope": (
                    "database tables, originals, event inputs and local research journals; "
                    "no provider credentials"
                ),
            },
        )
        for name, expected in hashes.items():
            if hashlib.sha256((target / name).read_bytes()).hexdigest() != expected:
                raise ValueError(f"Backup checksum mismatch: {name}")
    archive = shutil.make_archive(str(target), "zip", root_dir=target)
    print(
        json.dumps(
            {
                "directory": str(target),
                "archive": archive,
                "files_verified": len(hashes),
                "ledger_versions": len(original),
            },
            ensure_ascii=False,
        )
    )
    service.pool.shutdown()
    service.runner.close()


if __name__ == "__main__":
    main()
