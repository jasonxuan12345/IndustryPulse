from __future__ import annotations

from datetime import datetime

import plotly.graph_objects as go
import streamlit as st

from apps.dashboard.client import DashboardClient
from industrypulse.contracts import ResearchMode


def render(client: DashboardClient, at: datetime, mode: ResearchMode) -> None:
    st.subheader("行业六维景气")
    response = client.get("/v1/industries/CN_NBS_C31/state", as_of=at, mode=mode)
    dimensions = response.result["dimensions"]
    valid = {k: v["level"] for k, v in dimensions.items() if v["level"] is not None}
    if len(valid) >= 3:
        figure = go.Figure(
            go.Scatterpolar(r=list(valid.values()), theta=list(valid), fill="toself")
        )
        figure.update_layout(title="六维稳健标准分（训练期 median/MAD）")
        st.plotly_chart(figure, use_container_width=True)
    else:
        st.info("可评分维度不足，暂不绘制雷达。")
    for name, state in dimensions.items():
        st.write(
            f"{name} · 覆盖率 {state['coverage']:.0%} · "
            f"{'缺失' if state['level'] is None else state['level']}"
        )
        if state["missing_indicators"]:
            st.caption("；".join(state["missing_indicators"]))
