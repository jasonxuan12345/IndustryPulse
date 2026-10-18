"""Run: uv run streamlit run apps/dashboard/app.py."""

from __future__ import annotations

import sys
from datetime import datetime, time
from pathlib import Path

# Streamlit executes this file as a script; resolve the dashboard package from workspace root.
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from apps.dashboard.client import ApiClientError, DashboardClient, default_as_of  # noqa: E402
from apps.dashboard.components import MODE_LABELS, quality_banner  # noqa: E402
from apps.dashboard.pages import industry, macro, models, radar  # noqa: E402
from industrypulse.contracts import TZ_SHANGHAI, ResearchMode  # noqa: E402

st.set_page_config(page_title="IndustryPulse", page_icon="◉", layout="wide")
st.title("IndustryPulse")
st.caption("行业经济监测与预测研究 · G1 离线工作台")
with st.sidebar:
    st.header("研究上下文")
    mode = st.selectbox("历史研究模式", list(ResearchMode), format_func=lambda m: MODE_LABELS[m])
    day = st.date_input("决策日期", default_as_of().date())
    page = st.radio("页面", ["宏观总览", "行业雷达", "钢铁详情", "模型比较"])
    st.caption("DEMO · 使用冻结 G0 合成 fixture")
at = datetime.combine(day, time(17), TZ_SHANGHAI)
client = DashboardClient()
try:
    response = client.get("/v1/industries/CN_NBS_C31/state", as_of=at, mode=mode)
    quality_banner(response)
    {
        "宏观总览": macro.render,
        "行业雷达": radar.render,
        "钢铁详情": industry.render,
        "模型比较": models.render,
    }[page](client, at, mode)
except ApiClientError as exc:
    st.error(f"{exc.payload.code}: {exc.payload.message}")
    st.info(exc.payload.remediation)
except (ValueError, OSError) as exc:
    st.error(f"离线快照读取失败：{type(exc).__name__}")
finally:
    client.close()
