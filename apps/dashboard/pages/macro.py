from __future__ import annotations

from datetime import datetime

import streamlit as st

from apps.dashboard.client import DashboardClient
from apps.dashboard.components import source_link
from industrypulse.contracts import ResearchMode


def render(client: DashboardClient, at: datetime, mode: ResearchMode) -> None:
    st.subheader("宏观总览")
    st.info("制造业 PMI 和社融是宏观代理；尚无此日期的合成宏观观测。")
    st.subheader("可用时间与版本示例")
    response = client.get("/v1/series/cn.nbs.industry_c31.profit_ytd/history", as_of=at, mode=mode)
    for row in response.result["vintages"]:
        with st.expander(
            f"{row['observation_start']} · v{row['vintage_id']} · {row['value']} {row['unit']}"
        ):
            st.write(
                {
                    key: row[key]
                    for key in [
                        "publication_at",
                        "available_at",
                        "retrieved_at",
                        "release_evidence",
                        "value_evidence",
                        "missing_reason",
                    ]
                }
            )
            source_link(row.get("source_url"))
    if not response.result["vintages"]:
        st.warning("当前时点和模式下没有可见观测。")
