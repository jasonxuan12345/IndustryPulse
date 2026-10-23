"""真实联网验收（§39）：全量同步行情/宏观，输出 reports/data-coverage-latest.md。

在服务停止时运行，避免 DuckDB 文件锁：
    PYTHONPATH=src python scripts/verify_data_coverage.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from industrypulse.market.service import MarketService  # noqa: E402

REPORT = ROOT / "reports/data-coverage-latest.md"


def main() -> int:
    service = MarketService()
    lines = [
        "# 数据覆盖率验收报告",
        "",
        f"生成时间：{datetime.now().isoformat(timespec='seconds')}（Asia/Shanghai）",
        "",
        "本报告由 `scripts/verify_data_coverage.py` 真实联网生成，数字反映当次运行结果。",
        "",
    ]

    # 1. 历史行情同步
    symbols: list[str] = []
    for config in service.sectors_config["domains"].values():
        symbols.append(config["board"])
        symbols.append(config["etfs"]["primary"])
        symbols.extend(config["etfs"].get("alternatives", []))
    symbols += [item["symbol"] for item in service.sectors_config["benchmarks"].values()]
    kline_ok: dict[str, str] = {}
    for symbol in dict.fromkeys(symbols):
        try:
            bars = service._load_kline(symbol)
            kline_ok[symbol] = f"{len(bars)}bar→{bars[-1].date}"
        except Exception as exc:  # noqa: BLE001
            kline_ok[symbol] = f"FAIL:{str(exc)[:40]}"
    service._flush_bars()

    # 2. 市场总览
    overview = service.overview(wait=True)
    sectors = overview.get("sectors", [])
    filled_change = [s for s in sectors if s.get("change") is not None]
    filled_trend = [s for s in sectors if len(s.get("trend", [])) > 100]
    filled_heat = [s for s in sectors if s.get("heat") is not None]
    flow_ok = [s for s in sectors if s.get("net_flow") is not None]
    benchmarks = [b for b in overview.get("benchmarks", []) if b.get("price") is not None]

    # 3. 宏观同步
    macro_counts = service.sync_macro()
    service.health.persist()
    macro = service.macro()
    indicators = macro["indicators"]
    fresh = [i for i in indicators if i.get("status") == "fresh"]
    stale = [i for i in indicators if i.get("status") == "stale"]
    missing = [i for i in indicators if i.get("value") is None]

    # 4. 公司行情
    companies = service.company_quotes()
    quoted = [c for c in companies if c.get("price") is not None]

    # 5. 健康
    health = service.data_health()
    active = [h for h in health if h["status"] == "active"]
    failed = [h for h in health if h["status"] in ("failed", "blocked")]

    lines += [
        "## 结论速览",
        "",
        f"- 行业行情覆盖：**{len(filled_change)}/{len(sectors)}** 个领域有真实涨跌幅，"
        f"{len(filled_trend)} 个有 180 点趋势序列，{len(filled_heat)} 个有热度分",
        f"- 板块资金流（东财）：{len(flow_ok)}/{len(sectors)}（本网络对该接口间歇封锁，失败时 UI 明确标注并给出替代指标）",
        f"- 市场基准：{len(benchmarks)}/{len(overview.get('benchmarks', []))} 个指数有真实报价",
        f"- 宏观指标：{macro_counts['ok']} 个同步成功，其中最新数据 **fresh {len(fresh)} 项 / stale {len(stale)} 项**"
        f"（stale 为上游停止更新或滞后的序列，UI 显示期次日期）",
        f"- 公司行情：{len(quoted)}/{len(companies)} 个行业龙头有真实报价（现价/市值/PE/PB）",
        f"- 数据源健康：active {len(active)}，failed/blocked {len(failed)}（明细见 `/api/dashboard/data-health`）",
        "",
        "## 行业明细",
        "",
        "| 领域 | 涨跌幅% | 指数源 | 趋势点 | 热度 | 主力净流入(亿) | 最新日期 |",
        "|---|---|---|---|---|---|---|",
    ]
    for sector in sectors:
        lines.append(
            f"| {sector['name']} | {sector.get('change')} | {sector.get('index_source', '—')} "
            f"| {len(sector.get('trend', []))} | {sector.get('heat')} "
            f"| {sector.get('net_flow')} | {sector.get('latest_date', '—')} |"
        )
    lines += [
        "",
        "## 市场基准",
        "",
        "| 指数 | 最新价 | 涨跌% |",
        "|---|---|---|",
    ]
    for item in overview.get("benchmarks", []):
        lines.append(f"| {item['name']} | {item.get('price')} | {item.get('change_pct')} |")
    lines += [
        "",
        "## 宏观指标（最新期次）",
        "",
        "| 指标 | 最新值 | 单位 | 期次 | 状态 |",
        "|---|---|---|---|---|",
    ]
    for item in indicators:
        if item.get("value") is None:
            lines.append(f"| {item['name']} | — | | | missing |")
        else:
            lines.append(
                f"| {item['name']} | {item['value']} | {item.get('unit', '')} "
                f"| {item.get('period')} | {item.get('status')} |"
            )
    lines += [
        "",
        "## 公司行情（部分）",
        "",
        "| 公司 | 领域 | 现价 | 涨跌% | 总市值(亿) | PE | PB |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in quoted[:20]:
        quote = row["quote"] if "quote" in row else row
        lines.append(
            f"| {row['name']} | {row['sector']} | {quote.get('price')} "
            f"| {row.get('change_pct', quote.get('change_pct'))} | {quote.get('market_cap')} "
            f"| {quote.get('pe')} | {quote.get('pb')} |"
        )
    lines += [
        "",
        "## K 线历史回填",
        "",
        "```text",
    ]
    for symbol, status in kline_ok.items():
        lines.append(f"{symbol:<12} {status}")
    lines += [
        "```",
        "",
        "## 数据源健康",
        "",
        "| 源 | 状态 | 说明 |",
        "|---|---|---|",
    ]
    for item in health:
        lines.append(
            f"| {item['source_id']} | {item['status']} | {item.get('note', '')[:80]} |"
        )
    lines += [
        "",
        "## 已知缺口（诚实声明）",
        "",
        "- **东财板块资金流**：push2 系接口在本网络间歇 502/断连，属环境性封锁；",
        "  代码保留镜像重试，恢复后自动有数。UI 失败时显示原因并指向两融/Shibor/LPR 等替代指标。",
        "- **国家统计局结构化查询接口（data.stats.gov.cn）**：本网络 403（WAF UrlACL）→ BLOCKED；",
        "  公告页解析管道（datahub）不受影响，仍在研究链路中运行。",
        "- **美元兑人民币中间价**：上游序列停更于 2021-05，标 stale；后续接入东财外汇行情替代。",
        "- **LME/海外半导体（SOX 等）**：免登录稳定源未找到，标记 MANUAL/可选；",
        "  半导体行业以 A 股板块 + ETF + 国内宏观指标覆盖。",
        "- **行业专属基本面（粗钢产量/动力煤价格/锂价等）**：本轮未逐项接入，",
        "  研究链路的 NBS 公告序列（钢铁/煤炭/化工/有色已有 39 序列）继续有效；下一阶段按行业逐个补。",
        "",
    ]

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(f"report written: {REPORT}")
    print(
        json.dumps(
            {
                "sectors_filled": f"{len(filled_change)}/{len(sectors)}",
                "macro_ok": macro_counts,
                "macro_fresh": len(fresh),
                "macro_stale": len(stale),
                "companies_quoted": f"{len(quoted)}/{len(companies)}",
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
