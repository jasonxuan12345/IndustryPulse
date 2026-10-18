"""Common quality, clock, mode, and source evidence presentation."""

from __future__ import annotations

from typing import Any

import streamlit as st

from industrypulse.contracts import ApiResponse, ResearchMode

MODE_LABELS = {
    ResearchMode.STRICT_PIT: "STRICT PIT · 严格历史时点",
    ResearchMode.RECONSTRUCTED_PIT: "RECONSTRUCTED PIT · 保守重建",
    ResearchMode.EXPLORATORY: "EXPLORATORY · 非 PIT 验证",
}


def quality_banner(response: ApiResponse[Any]) -> None:
    st.info(MODE_LABELS[response.research_mode])
    st.caption(f"决策时点：{response.as_of.isoformat()} · 快照：{response.snapshot_id}")
    for warning in response.warnings:
        st.warning(warning.message)
    st.caption(
        f"覆盖率 {response.data_quality.coverage:.0%} · "
        f"历史版本核验比例 {response.data_quality.vintage_verified_ratio:.0%}"
    )


def source_link(url: str | None, text: str = "原始出处") -> None:
    if url and url.startswith(("https://", "http://")):
        st.link_button(text, url)
    else:
        st.caption("此合成记录未提供可访问的真实原件")
