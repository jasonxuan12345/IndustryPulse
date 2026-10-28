"""Start the portable server or check an existing CI container without API calls."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


def check(base: str) -> None:
    for _ in range(60):
        try:
            with urlopen(base + "/api/research/mvp/report", timeout=2) as response:
                report = json.load(response)
            assert report["status"] in {"not_run", "completed", "degraded"}
            break
        except (URLError, TimeoutError, ConnectionError):
            time.sleep(0.5)
    else:
        raise RuntimeError("Dashboard did not become ready")
    for path in ("/", "/research", "/research/quant", "/dashboard-assets/research-mvp.js"):
        with urlopen(base + path, timeout=5) as response:
            assert response.status == 200 and response.read()
    print("PASS: portable server, UI assets and research API")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="")
    args = parser.parse_args()
    if args.url:
        check(args.url.rstrip("/"))
        return
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "INDUSTRYPULSE_NEWS_AUTO_FETCH": "0", "INDUSTRYPULSE_MARKET_DATA": "0"}
    process = subprocess.Popen(
        [sys.executable, str(root / "scripts/start_dashboard.py"), "--port", "18765"],
        cwd=root,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        check("http://127.0.0.1:18765")
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


if __name__ == "__main__":
    main()
