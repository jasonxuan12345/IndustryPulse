"""导出合同工件到 contracts/ 目录（C-002/C-008 生成物入口）。

默认写出；``--check`` 仅比对，漂移时退出码 1（供 CI 与守护测试使用）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from industrypulse.contracts import export

REPO_ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="只比对不写出；漂移退出码 1")
    args = parser.parse_args(argv)

    if args.check:
        problems = export.check(REPO_ROOT)
        for line in problems:
            print(line, file=sys.stderr)
        if problems:
            hint = "python scripts/export_contracts.py"
            print(f"contracts 生成物漂移 {len(problems)} 处，请运行 {hint}", file=sys.stderr)
            return 1
        print("contracts 生成物与源码同步")
        return 0

    written = export.write_all(REPO_ROOT)
    print(f"已写出 {len(written)} 个合同工件到 contracts/（版本见 MANIFEST.json）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
