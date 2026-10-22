"""Causal turning points and online mean-shift monitoring."""

from collections.abc import Sequence


def turning_points(
    values: Sequence[float], threshold: float = 0.5
) -> list[dict[str, float | int | str]]:
    events: list[dict[str, float | int | str]] = []
    state = "neutral"
    positive, negative = 0.0, 0.0
    for index, value in enumerate(values):
        previous = values[index - 1] if index else value
        next_state = (
            "expansion" if value > threshold else "contraction" if value < -threshold else "neutral"
        )
        if next_state != state:
            events.append({"index": index, "kind": next_state, "value": value})
            state = next_state
        change = value - previous
        positive = max(0.0, positive + change - 0.1)
        negative = min(0.0, negative + change + 0.1)
        if positive > threshold or negative < -threshold:
            events.append(
                {
                    "index": index,
                    "kind": "cusum_up" if positive > threshold else "cusum_down",
                    "value": value,
                }
            )
            positive, negative = 0.0, 0.0
    return events


def margin_proxy(
    steel_price: float | None,
    ore_price: float | None,
    coke_price: float | None,
    ore_weight: float = 1.6,
    coke_weight: float = 0.45,
) -> float | None:
    if any(value is None for value in (steel_price, ore_price, coke_price)):
        return None
    assert steel_price is not None and ore_price is not None and coke_price is not None
    return steel_price - ore_weight * ore_price - coke_weight * coke_price
