"""Fixed-input local timing and peak-memory record."""

from __future__ import annotations

import time
import tracemalloc
from datetime import datetime

from industrypulse.contracts import TZ_SHANGHAI, ResearchMode
from industrypulse.services.workbench import Workbench, write_json


def main() -> None:
    service = Workbench()
    tracemalloc.start()
    start = time.perf_counter()
    catalog = service.catalog()
    report = service.economy(
        "steel-g2", ResearchMode.RECONSTRUCTED_PIT, datetime(2026, 10, 22, 17, tzinfo=TZ_SHANGHAI)
    )
    elapsed = time.perf_counter() - start
    _, peak = tracemalloc.get_traced_memory()
    result = {
        "dataset": catalog["stats"],
        "snapshot": report["report"]["snapshot_id"],
        "operation": "catalog + three-month economy",
        "seconds": elapsed,
        "peak_memory_mib": peak / 1024**2,
    }
    write_json(service.output / "performance.json", result)
    print(result)


if __name__ == "__main__":
    main()
