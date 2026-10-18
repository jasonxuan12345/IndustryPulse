from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from apps.dashboard.client import DashboardClient, default_as_of  # noqa: E402
from industrypulse.contracts import ROUTES, ResearchMode  # noqa: E402


def test_all_frozen_get_routes_available_offline() -> None:
    client = DashboardClient()
    for mode in ResearchMode:
        for route in ROUTES:
            if route.method == "GET":
                response = client.get(
                    route.path.replace("{id}", "CN_NBS_C31"), as_of=default_as_of(), mode=mode
                )
                assert response.research_mode == mode and response.warnings[0].code == "DEMO_DATA"
    with pytest.raises(ValueError):
        client.get("/bad", as_of=default_as_of(), mode=ResearchMode.STRICT_PIT)
    client.close()


def test_streamlit_pages_all_render_without_backend() -> None:
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(str(ROOT / "apps/dashboard/app.py")).run(timeout=20)
    assert not app.exception
    for page in ["行业雷达", "钢铁详情", "模型比较", "宏观总览"]:
        app.sidebar.radio[0].set_value(page).run(timeout=20)
        assert not app.exception
    app.sidebar.selectbox[0].set_value(ResearchMode.EXPLORATORY).run(timeout=20)
    assert not app.exception
