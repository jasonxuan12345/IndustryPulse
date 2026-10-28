"""Portable server entry point for Windows, macOS and Linux source checkouts."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="IndustryPulse browser dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    sys.path.insert(0, str(root / "src"))
    import uvicorn

    env = root / ".env"
    uvicorn.run(
        "industrypulse.api.runtime:app",
        host=args.host,
        port=args.port,
        env_file=str(env) if env.exists() else None,
    )


if __name__ == "__main__":
    main()
