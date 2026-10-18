"""G0 ownership remains an executable architecture gate."""

import ast
from pathlib import Path


def test_business_lanes_only_depend_on_own_lane_contracts_and_core() -> None:
    root = Path(__file__).resolve().parents[2] / "src/industrypulse"
    groups = [
        {"datahub"},
        {"ai_research"},
        {"repositories"},
        {"taxonomy"},
        {"economy"},
        {"features", "forecast"},
        {"research"},
        {"investment"},
        {"jobs", "core"},
    ]
    for group in groups:
        allowed = group | {"contracts", "core"}
        for package in group:
            for path in (root / package).rglob("*.py"):
                for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
                    modules = (
                        [node.module]
                        if isinstance(node, ast.ImportFrom)
                        else (
                            [alias.name for alias in node.names]
                            if isinstance(node, ast.Import)
                            else []
                        )
                    )
                    for module in modules:
                        if module and module.startswith("industrypulse."):
                            dependency = module.split(".")[1]
                            assert dependency in allowed, f"{path}: forbidden dependency {module}"
