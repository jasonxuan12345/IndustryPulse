from __future__ import annotations

from datetime import UTC, datetime, timedelta
from threading import Event

import pytest
from fastapi.testclient import TestClient

from industrypulse.api.app import create_app
from industrypulse.api.dashboard import Feed
from industrypulse.intelligence.catalog import Catalog, Domain, Industry, Source, load_catalog
from industrypulse.intelligence.feeds import make_item, parse_wallstreetcn, parse_xml
from industrypulse.intelligence.service import NewsService, SourceCache, matches

NOW = datetime.now(UTC)


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setenv("INDUSTRYPULSE_NEWS_AUTO_FETCH", "0")
    catalog = Catalog(
        domains=[
            Domain(
                id="semi",
                name="半导体",
                industries=[
                    Industry(id="chips", name="芯片", keywords=["芯片", "chip"]),
                    Industry(id="memory", name="存储", keywords=["内存", "memory"]),
                ],
            ),
            Domain(
                id="ai",
                name="人工智能",
                industries=[
                    Industry(id="models", name="模型", keywords=["模型", "LLM"]),
                ],
            ),
        ],
        sources=[
            Source(id="first", name="第一源", domains=["semi"], recommended=True),
            Source(id="second", name="第二源", domains=["semi"], recommended=True),
            Source(id="ai", name="AI源", domains=["ai"], recommended=True),
        ],
    )
    result = NewsService(tmp_path, catalog)
    yield result
    result.close()


def test_catalog_has_unique_sources_and_valid_industries():
    catalog = load_catalog()
    assert len(catalog.sources) >= 100
    assert len(catalog.domains) == 12
    assert sum(len(domain.industries) for domain in catalog.domains) == 30
    assert len({source.id for source in catalog.sources}) == len(catalog.sources)
    domain_ids = {domain.id for domain in catalog.domains}
    for source in catalog.sources:
        assert source.url.startswith("https://") or source.route.startswith("/")
        assert set(source.domains) <= domain_ids | {"*"}


def test_rss_atom_dates_and_safe_text():
    rss = b"""<rss><channel><item><title>New chip</title>
    <link>https://example.com/news?utm_source=rss</link>
    <description><![CDATA[<p>A chip</p><script>bad()</script>]]></description>
    <pubDate>Tue, 20 Oct 2026 10:00:00 GMT</pubDate></item></channel></rss>"""
    item = parse_xml(rss, "https://example.com/feed", NOW)[0]
    assert item.summary == "A chip"
    assert item.url == "https://example.com/news"
    assert (
        parse_xml(rss.replace(b"A chip", b"A\x0c chip"), "https://example.com", NOW)[0].summary
        == "A chip"
    )
    assert item.published_at == datetime(2026, 10, 20, 10, tzinfo=UTC)
    atom = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry>
    <title>New model</title><link rel="alternate" href="/model"/>
    <updated>2026-10-20T12:00:00+08:00</updated><summary>AI</summary>
    </entry></feed>"""
    assert parse_xml(atom, "https://example.com/", NOW)[0].url == "https://example.com/model"
    with pytest.raises(ValueError, match="RSS"):
        parse_xml(b"<html><body>challenge</body></html>", "https://example.com", NOW)
    assert make_item("bad", "", "javascript:alert(1)", "", NOW) is None


def test_public_json_and_unknown_dates():
    items = parse_wallstreetcn(
        {
            "code": 20000,
            "data": {
                "items": [
                    {
                        "resource": {
                            "title": "chip news",
                            "uri": "https://example.com/a",
                            "display_time": 1792490400,
                        }
                    },
                    {"resource_type": "ad", "resource": {}},
                ]
            },
        },
        NOW,
    )
    assert len(items) == 1
    assert items[0].time_kind == "published"
    unknown = make_item("chip", "", "https://example.com/b", "invalid", NOW)
    assert unknown.time_kind == "first_seen" and unknown.published_at == NOW
    with pytest.raises(ValueError):
        parse_wallstreetcn({"code": 403}, NOW)


def test_per_industry_selection_persists_and_deduplicates(service):
    item = make_item("芯片与内存新进展", "", "https://example.com/a", NOW.isoformat(), NOW)
    service.cache = {
        name: SourceCache(items=[item], last_success=NOW) for name in ["first", "second"]
    }
    feed = Feed.model_validate(service.feed())
    assert feed.mode == "live" and len(feed.stories) == 1
    assert feed.stories[0].industry_ids == ["chips", "memory"]
    assert feed.stories[0].source_ids == ["first", "second"]
    assert feed.sectors[0].change is None and feed.sectors[0].heat is None
    settings = service.settings.model_copy(deep=True)
    settings.selections = {"chips": ["first"], "memory": [], "models": []}
    service.save(settings)
    restored = NewsService(service.directory, service.catalog)
    assert restored.settings.selections["memory"] == []
    assert Feed.model_validate(service.feed()).stories[0].industry_ids == ["chips"]
    settings.selections["chips"] = ["ai"]
    with pytest.raises(ValueError):
        service.save(settings)


def test_failed_source_keeps_old_items_and_success_updates_cache(service, monkeypatch):
    old = make_item("chip", "", "https://example.com/a", "", NOW - timedelta(days=1))
    service.cache["first"] = SourceCache(items=[old], last_success=NOW - timedelta(days=1))

    def fetch(source, rsshub_base):
        if source.id == "second":
            raise ValueError("offline")
        return [make_item("chip", "", "https://example.com/a", "", NOW)]

    monkeypatch.setattr(service, "_fetch", fetch)
    service.cache["second"] = SourceCache(items=[old], last_success=NOW)
    assert service.refresh(["first", "second"], force=True)
    service.worker.join(5)
    assert not service.worker.is_alive()
    assert service.cache["first"].items[0].published_at == old.published_at
    assert service.cache["second"].items == [old]
    assert service.cache["second"].error == "offline"
    assert (
        next(source for source in service.catalog_payload()["sources"] if source["id"] == "second")[
            "status"
        ]["state"]
        == "stale"
    )
    restored = NewsService(service.directory, service.catalog)
    assert restored.cache["second"].error == "offline"


def test_refresh_is_single_flight_and_settings_apply_while_collecting(service, monkeypatch):
    gate = Event()

    def fetch(source, rsshub_base):
        gate.wait(3)
        return [make_item("chip", "", "https://example.com/a", NOW.isoformat(), NOW)]

    monkeypatch.setattr(service, "_fetch", fetch)
    service.refresh(["first"], force=True)
    try:
        assert not service.refresh(["first"], force=True)
        settings = service.settings.model_copy(deep=True)
        settings.selections = {}
        service.save(settings)
    finally:
        gate.set()
        service.worker.join(5)
    assert service.feed()["stories"] == []
    assert not service.refresh()
    assert matches("An EV launch", ["EV"])
    assert not matches("The revolution", ["EV"])


def test_sources_api_settings_mode_switch_and_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("INDUSTRYPULSE_NEWS_DIR", str(tmp_path / "news"))
    monkeypatch.setenv("INDUSTRYPULSE_NEWS_AUTO_FETCH", "0")
    monkeypatch.setenv("INDUSTRYPULSE_FEED_FILE", str(tmp_path / "import.json"))
    monkeypatch.delenv("INDUSTRYPULSE_FEED_URL", raising=False)
    with TestClient(create_app()) as client:
        catalog = client.get("/api/dashboard/sources").json()
        assert len(catalog["sources"]) >= 100
        assert client.get("/api/dashboard/feed").json()["mode"] == "live"
        settings = catalog["settings"]
        settings.update(mode="demo", selections={"chip-design": []})
        assert client.post("/api/dashboard/sources/settings", json=settings).status_code == 200
        assert client.get("/api/dashboard/feed").json()["mode"] == "demo"
        settings["mode"] = "builtin"
        client.post("/api/dashboard/sources/settings", json=settings)
        assert client.get("/api/dashboard/feed").json()["stories"] == []
        assert (
            client.post(
                "/api/dashboard/sources/refresh", json={"source_ids": ["missing"]}
            ).status_code
            == 422
        )
    with TestClient(create_app()) as client:
        assert client.get("/api/dashboard/sources").json()["settings"]["selections"] == {
            "chip-design": []
        }
