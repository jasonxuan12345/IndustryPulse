"""Event-time forecasts using the existing model families and released labels only.

No hyperparameter search takes place here. Monthly walk-forward diagnostics keep
the current prediction separate from any claim of out-of-sample advantage.
"""

from __future__ import annotations

import math
import warnings
from datetime import date, datetime, time, timedelta
from typing import Any

from industrypulse.contracts import TZ_SHANGHAI, FeatureFrame, FeatureRow, LabeledFeatureFrame
from industrypulse.contracts.model import TargetFrame, TargetRow, TargetSpec
from industrypulse.forecast.models import InsufficientEvidence
from industrypulse.forecast.registry import create_model
from industrypulse.services import ledger
from industrypulse.services.chain_demo import MODE, EvidenceReplay
from industrypulse.services.research_run import _month_ends, make_experiment_spec


def frame(iid: str, rows: list[FeatureRow], snapshot: str) -> FeatureFrame:
    return FeatureFrame(
        industry_id=iid,
        snapshot_id=snapshot,
        feature_spec_version="live-event-v1",
        schema_hash=ledger._hash(sorted(rows[0].features)),
        research_mode=MODE,
        built_at=rows[-1].decision_as_of,
        rows=rows,
    )


def price_features(bundle: dict[str, Any], aid: str, at: datetime) -> dict[str, float | None]:
    """Use closed daily bars; do not manufacture returns across action barriers."""
    from industrypulse.contracts.domain import CorporateAction, MarketBar
    from industrypulse.services.etf_audit import find_barriers

    end = at.date() if at.time() >= time(15) else at.date() - timedelta(days=1)
    result: dict[str, float | None] = {
        "relative_20d": None,
        "relative_60d": None,
        "volatility_20d": None,
    }
    lines = {}
    for asset in (aid, bundle["policy"]["benchmark_id"]):
        bars = [
            MarketBar.model_validate(b)
            for b in bundle["bars"]
            if b["asset_id"] == asset and date.fromisoformat(b["trade_date"]) <= end
        ]
        bars.sort(key=lambda b: b.trade_date)
        actions = [
            CorporateAction.model_validate(a)
            for a in bundle["actions"]
            if a["asset_id"] == asset and date.fromisoformat(a["ex_date"]) <= end
        ]
        lines[asset] = (bars, actions)
    if not aid or not lines[aid][0]:
        return result
    asset_bars, actions = lines[aid]
    benchmark_bars, benchmark_actions = lines[bundle["policy"]["benchmark_id"]]
    common = sorted({b.trade_date for b in asset_bars} & {b.trade_date for b in benchmark_bars})
    blocked = {date.fromisoformat(e["day"]) for e in find_barriers(asset_bars, actions)}
    blocked |= {
        date.fromisoformat(e["day"]) for e in find_barriers(benchmark_bars, benchmark_actions)
    }

    def ret(bars: Any, acts: Any, start: date, finish: date) -> float:
        closes = {b.trade_date: float(b.close) for b in bars}
        cash = sum(
            float(a.distribution_per_unit)
            for a in acts
            if a.action_type == "cash_dividend" and start < a.ex_date <= finish
        )
        return (closes[finish] + cash) / closes[start] - 1

    for count in (20, 60):
        if len(common) > count and not any(common[-count - 1] < d <= common[-1] for d in blocked):
            result[f"relative_{count}d"] = ret(
                asset_bars, actions, common[-count - 1], common[-1]
            ) - ret(benchmark_bars, benchmark_actions, common[-count - 1], common[-1])
    if len(common) > 20 and not any(common[-21] < d <= common[-1] for d in blocked):
        daily = [
            ret(asset_bars, actions, a, b)
            for a, b in zip(common[-21:-1], common[-20:], strict=True)
        ]
        mean = sum(daily) / len(daily)
        result["volatility_20d"] = math.sqrt(sum((r - mean) ** 2 for r in daily) / len(daily))
    return result


def feature(
    replay: EvidenceReplay, bundle: dict[str, Any], spec: dict[str, Any], at: datetime
) -> FeatureRow | None:
    view = replay.select(at)
    facts = [view.get(sid, [])[-1] for sid in spec["series"] if view.get(sid)]
    values: dict[str, float | None] = {sid: None for sid in spec["series"]}
    values.update({r.series_id: float(r.value) if r.value is not None else None for r in facts})
    # Missing data stay missing; never carry older valid periods over a missing latest row.
    if not any(v is not None for v in values.values()):
        return None
    if spec.get("asset_id"):
        values.update(price_features(bundle, spec["asset_id"], at))
    return FeatureRow(
        decision_as_of=at,
        industry_id=spec["industry_id"],
        features=values,
        missing_features=[k for k, v in values.items() if v is None],
        max_ancestor_available_at=max(replay.available(r) for r in facts),
    )


def released_label(
    replay: EvidenceReplay,
    spec: dict[str, Any],
    row: FeatureRow,
    target: str,
    at: datetime,
    bundle: dict[str, Any],
) -> tuple[float, datetime] | None:
    if target == "T3":
        aid, bid = spec.get("asset_id"), bundle["policy"]["benchmark_id"]
        if not aid:
            return None
        lines = {
            a: {
                date.fromisoformat(b["trade_date"]): b for b in bundle["bars"] if b["asset_id"] == a
            }
            for a in (aid, bid)
        }
        common = sorted(set(lines[aid]) & set(lines[bid]))
        eligible = [d for d in common if d > row.decision_as_of.date()]
        if len(eligible) < 63:
            return None
        entry, exit_day = eligible[0], eligible[62]
        released = datetime.combine(exit_day, time(15), tzinfo=TZ_SHANGHAI)
        if released >= at:
            return None
        from industrypulse.contracts.domain import CorporateAction, MarketBar
        from industrypulse.services.etf_audit import find_barriers

        returns = []
        cost = (bundle["policy"]["commission_bps"] + bundle["policy"]["slippage_bps"]) / 10000
        for asset in (aid, bid):
            bars = [MarketBar.model_validate(b) for d, b in lines[asset].items() if d <= exit_day]
            acts = [
                CorporateAction.model_validate(a)
                for a in bundle["actions"]
                if a["asset_id"] == asset and date.fromisoformat(a["ex_date"]) <= exit_day
            ]
            expected = [d for d in sorted(lines[bid]) if entry <= d <= exit_day]
            if any(d not in lines[asset] for d in expected):
                return None
            if any((b - a).days > 10 for a, b in zip(expected, expected[1:], strict=False)):
                return None
            if any(
                float(lines[asset][d]["volume"]) <= 0 or lines[asset][d]["adj_method"] != "none"
                for d in expected
            ):
                return None
            if any(
                entry < date.fromisoformat(e["day"]) <= exit_day for e in find_barriers(bars, acts)
            ):
                return None
            if any(
                a.action_type != "cash_dividend" and entry < a.ex_date <= exit_day for a in acts
            ):
                return None
            paid = sum(
                float(a.distribution_per_unit)
                for a in acts
                if a.action_type == "cash_dividend" and entry < a.ex_date <= exit_day
            )
            returns.append(
                (float(lines[asset][exit_day]["close"]) * (1 - cost) + paid)
                / (float(lines[asset][entry]["open"]) * (1 + cost))
                - 1
            )
        return float(returns[0] > returns[1]), released
    sid = spec.get("iva_id") if target == "T1" else spec["profit_id"]
    if not sid or row.features.get(sid) is None:
        return None
    target_period = ledger.add_months(
        row.decision_as_of.date().replace(day=1), 3 if target == "T1" else 1
    )
    # Fix the first released vintage as the label; a later revision cannot rewrite past targets.
    candidates = [
        r
        for r in replay.rows
        if r.series_id == sid
        and r.observation_start == target_period
        and replay.available(r) < at
        and r.value is not None
    ]
    if not candidates:
        return None
    first = min(candidates, key=lambda r: (replay.available(r), r.vintage_id))
    assert first.value is not None
    base = row.features[sid]
    assert base is not None
    return float(float(first.value) > base), replay.available(first)


def forecast(bundle: dict[str, Any], spec: dict[str, Any], at: datetime) -> dict[str, Any]:
    replay = EvidenceReplay(bundle)
    iid = spec["industry_id"]
    current = feature(replay, bundle, spec, at)
    output: dict[str, Any] = {
        "industry_id": iid,
        "as_of": at.isoformat(),
        "targets": [],
        "status": "NOT_VALIDATED",
        "calibration": "UNCALIBRATED",
    }
    if current is None:
        return {**output, "error": "没有可见特征"}
    months = _month_ends(date(2021, 12, 1), at.date() - timedelta(days=1))
    history = [
        r
        for d in months
        if (r := feature(replay, bundle, spec, datetime.combine(d, time(17), tzinfo=TZ_SHANGHAI)))
    ]

    def sealed(day: date) -> bool:
        return any(
            date.fromisoformat(h["start"]) <= day <= date.fromisoformat(h["end"])
            for h in bundle.get("holdouts", [])
        )

    history = [r for r in history if not sealed(r.decision_as_of.date())]
    snap = ledger._hash({"rows": bundle["vintages"], "policy": bundle["policy"]})
    for target in ("T1", "T2", "T3"):
        item: dict[str, Any] = {
            "target": target,
            "meaning": {
                "T1": "三个月后工业增加值同比高于当前可见值的概率",
                "T2": "下一个日历月利润累计同比高于当前可见值的概率（缺期不跨越）",
                "T3": "次交易日开盘起63个交易日费后超额收益为正的概率",
            }[target],
            "models": [],
            "edge": "INSUFFICIENT_EVIDENCE",
        }
        if target == "T1" and not spec.get("iva_id"):
            item["error"] = "本行业未配置可核验的工业增加值序列"
            output["targets"].append(item)
            continue
        labels = {
            r.decision_as_of: released_label(replay, spec, r, target, at, bundle) for r in history
        }
        labels = {
            key: value if value and not sealed(value[1].date()) else None
            for key, value in labels.items()
        }

        def predict(
            model_name: str,
            decision: FeatureRow,
            *,
            labels: Any = labels,
            target: str = target,
            item: Any = item,
        ) -> tuple[dict[str, Any], float | None]:
            cutoff = decision.decision_as_of - timedelta(microseconds=1)
            samples = [
                (r, lab)
                for r in history
                if r.decision_as_of < cutoff
                and (lab := labels[r.decision_as_of])
                and lab[1] < cutoff
            ]
            if len(samples) < bundle["policy"]["minimum_training_rows"]:
                raise InsufficientEvidence(f"仅{len(samples)}个已发布标签，最低24")
            rows = [r for r, _ in samples]
            features = frame(iid, rows, snap)
            train = LabeledFeatureFrame(
                features=features,
                labels=[lab[0] for _, lab in samples],
                label_available_at=[lab[1] for _, lab in samples],
            )
            definition = TargetSpec(
                target_id=target,
                definition_version="live-v1",
                industry_id=iid,
                horizon_months=1 if target == "T2" else 3,
                baselines=["M00_LAST", "M01_SEASONAL", "M02_AR"],
                label_kind="direction_up",
                formula=item["meaning"],
                missing_policy="UNSCORABLE",
                description=item["meaning"],
            )
            config = make_experiment_spec(
                experiment_id="event-" + iid + "-" + target,
                features=features,
                targets=TargetFrame(
                    spec=definition,
                    rows=[
                        TargetRow(
                            industry_id=iid,
                            decision_as_of=r.decision_as_of,
                            target_period=r.decision_as_of.strftime("%Y-%m"),
                            value=lab[0],
                            label_available_at=lab[1],
                        )
                        for r, lab in samples
                    ],
                ),
                train_end=cutoff.date(),
                outer_start=decision.decision_as_of.date() + timedelta(days=1),
                outer_end=decision.decision_as_of.date() + timedelta(days=2),
                feature_list=list(current.features),
            )
            config = config.model_copy(
                update={
                    "train_cutoff": cutoff,
                    "target_definition": target + "@live-v1",
                    "created_at": cutoff,
                    "code_sha": ledger._hash(bundle.get("code_sha256", {})),
                    "dependencies_hash": ledger._hash(bundle.get("runtime_versions", {})),
                }
            )
            model = create_model(model_name, {})
            from sklearn.exceptions import ConvergenceWarning

            with warnings.catch_warnings():
                warnings.simplefilter("error", ConvergenceWarning)
                try:
                    artifact = model.fit(train, config)
                except ConvergenceWarning as exc:
                    raise InsufficientEvidence("拟合未收敛，拒绝使用该次概率") from exc
            result = (
                model.predict(frame(iid, [decision], snap), artifact)
                .predictions[0]
                .model_dump(mode="json")
            )
            result.update(
                training_rows=len(samples),
                artifact_sha256=artifact.sha256,
                horizon_months=1 if target == "T2" else 3,
            )
            label = labels.get(decision.decision_as_of)
            return result, label[0] if label else None

        for name in bundle["policy"]["models"]:
            model_out: dict[str, Any] = {"model": name, "oos": []}
            try:
                model_out["prediction"], _ = predict(name, current)
                # Last 12 matured monthly decisions, each trained at its own event time.
                matured = [r for r in history if labels[r.decision_as_of]]
                for row in matured[-12:]:
                    try:
                        pred, label = predict(name, row)
                        model_out["oos"].append(
                            {
                                "as_of": row.decision_as_of.isoformat(),
                                "probability": pred["predicted_probability"],
                                "label": label,
                                "training_rows": pred["training_rows"],
                            }
                        )
                    except (ValueError, RuntimeError) as exc:
                        model_out.setdefault("oos_failures", []).append(str(exc)[:150])
                oos = model_out["oos"]
                model_out["brier"] = (
                    sum((r["probability"] - r["label"]) ** 2 for r in oos) / len(oos)
                    if oos
                    else None
                )
            except (ValueError, RuntimeError) as exc:
                model_out["error"] = str(exc)[:200]
            item["models"].append(model_out)
        # All mandatory baselines must have the same complete OOS dates. Small/noisy
        # diagnostic samples cannot promote a model into an investment signal.
        dates = [set(r["as_of"] for r in m["oos"]) for m in item["models"][:3]]
        item["common_oos"] = len(set.intersection(*dates)) if dates else 0
        item["edge"] = (
            "NOT_VALIDATED"
            if item["common_oos"] >= bundle["policy"]["minimum_oos_rows"]
            else "INSUFFICIENT_EVIDENCE"
        )
        output["targets"].append(item)
    return output
