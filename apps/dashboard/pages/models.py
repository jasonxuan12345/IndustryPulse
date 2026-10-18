from __future__ import annotations

from datetime import datetime

import streamlit as st

from apps.dashboard.client import DashboardClient
from industrypulse.contracts import ResearchMode


def render(client: DashboardClient, at: datetime, mode: ResearchMode) -> None:
    st.subheader("模型比较 · M00–M04")
    response = client.get("/v1/experiments/offline-demo", as_of=at, mode=mode)
    result = response.result
    comparison = result.get("comparison", result)
    entries = comparison.get("models", {})
    if not entries:
        st.warning("NOT_VALIDATED · 暂无冻结的真实 OOS 结果，不提供推荐。")
    for model_id, record in entries.items():
        with st.expander(model_id):
            st.write(record.get("overall", {}))
            st.write(
                {
                    "共同OOS样本数": comparison.get("common_oos_count"),
                    "Brier改善率": record.get("relative_brier_improvement"),
                    "校准": record.get("calibration_status", "uncalibrated"),
                    "状态": record.get("status", "NOT_VALIDATED"),
                }
            )
            st.write(record.get("by_year", {}))
            if record.get("status") != "VALIDATED":
                st.caption("未通过验证，不提供推荐")
    st.write("比较使用共同样本外时点，并报告 Brier、基准改善率、分年误差和校准状态。")
    st.caption("Brier 越低越好；单类样本 AUC 为 NA；未校准概率需单独标记。")
