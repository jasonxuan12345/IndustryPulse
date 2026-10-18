from __future__ import annotations

from datetime import datetime

import streamlit as st

from apps.dashboard.client import DashboardClient
from apps.dashboard.pages.macro import render as render_history
from industrypulse.contracts import ResearchMode


def render(client: DashboardClient, at: datetime, mode: ResearchMode) -> None:
    st.subheader("钢铁 · CN_NBS_C31")
    st.caption("统计行业：黑色金属冶炼和压延加工业；证券行业与 ETF 口径独立。")
    st.subheader("产业链（合成知识示例）")
    for relation in client.fixture_data("steel_taxonomy")["relations"]:
        st.write(f"{relation['upstream_id']} → {relation['downstream_id']}")
        st.caption(f"依据：{relation['source_ref']} · 生效：{relation['valid_from']}")
    st.subheader("指标方向与解释")
    for indicator in client.get("/v1/industries/CN_NBS_C31/series", as_of=at, mode=mode).result:
        st.write(
            f"{indicator['description']} · {indicator['dimension']} · "
            f"{indicator['economic_direction']}"
        )
        st.caption(f"角色：{indicator['role']} · 序列：{', '.join(indicator['series_ids'])}")
    render_history(client, at, mode)
