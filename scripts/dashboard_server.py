"""Server entry point owned by the desktop launcher's Windows job."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    options = parser.parse_args()
    directory = Path(__file__).resolve().parents[1] / "artifacts/dashboard"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "server.log").open("a", encoding="utf-8", buffering=1) as log:
        sys.stdout = sys.stderr = log
        uvicorn.run("industrypulse.api.runtime:app", host="127.0.0.1", port=options.port)


if __name__ == "__main__":
    main()
