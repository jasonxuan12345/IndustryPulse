from __future__ import annotations

import httpx
import pytest

from industrypulse.datahub.extended import akshare_smoke, customs_import, fred_observations


def test_fred_realtime_query_and_pagination():
    def transport(request):
        assert (
            request.url.params["realtime_start"]
            == request.url.params["realtime_end"]
            == "2020-01-01"
        )
        offset = int(request.url.params["offset"])
        return httpx.Response(
            200,
            json={
                "count": 2,
                "observations": [
                    {
                        "date": f"2019-0{offset + 1}-01",
                        "value": "1.2",
                        "realtime_start": "2020-01-01",
                    }
                ],
            },
        )

    from datetime import date

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        result = fred_observations("TEST", date(2020, 1, 1), "key", client)
    assert len(result["observations"]) == 2


def test_customs_hs_versions_units_and_backup_drift():
    result = customs_import(
        [{"hs_code": "7208", "hs_version": "2022", "year": 2023, "unit": "kg", "value": "2500"}]
    )
    assert result[0]["quantity_tonnes"] == "2.5"
    with pytest.raises(ValueError):
        customs_import([{"hs_code": "7208", "year": 2023, "unit": "吨", "value": "1"}])
    assert akshare_smoke([{"date": "2020-01-01"}])["status"] == "SCHEMA_DRIFT"
