"""Read-only live provider probe; never prints credentials."""

import yaml

from industrypulse.services.research_mvp import WebTools
from industrypulse.services.workbench import ROOT, write_json

config = yaml.safe_load((ROOT / "config/research/mvp.yaml").read_text(encoding="utf-8"))
tools = WebTools(config)
result = {}
for name, action in (
    ("search", lambda: tools.search("煤炭 利润 site:stats.gov.cn before:2026-10-29")),
    ("model", lambda: tools.ask('返回JSON {"ok":true}')),
):
    try:
        result[name] = {"status": "ok", "result": action()}
    except Exception as exc:
        result[name] = {"status": "failed", "error": type(exc).__name__}
        if hasattr(exc, "response"):
            result[name]["http_status"] = exc.response.status_code
write_json(ROOT / "artifacts/mvp-live-probe.json", result)
print({name: {k: v for k, v in item.items() if k != "result"} for name, item in result.items()})
