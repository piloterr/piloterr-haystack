"""Offline tests: requests go through an httpx.MockTransport. Live tests need PILOTERR_API_KEY."""

from __future__ import annotations

import os
import re
from collections.abc import Callable

import httpx
import pytest
from haystack import Pipeline
from haystack.components.converters import HTMLToDocument
from haystack.utils import Secret

from haystack_integrations.components.fetchers.piloterr import PiloterrFetcher
from haystack_integrations.components.fetchers.piloterr import fetcher as fetcher_module
from haystack_integrations.components.websearch.piloterr import PiloterrError, PiloterrWebSearch
from haystack_integrations.components.websearch.piloterr import piloterr as search_module

API_KEY = "test-piloterr-key-placeholder"  # pragma: allowlist secret
ROWS = [
    {"position": 1, "title": "Haystack", "link": "https://haystack.deepset.ai/", "snippet": "LLM framework."},
    {"position": 2, "title": "Only a title", "link": "https://example.com/"},
    "not an object",
]
PAGE = "<html><head><title>Example Domain</title></head><body><p>For use in examples.</p></body></html>"

Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture
def mock_http(monkeypatch: pytest.MonkeyPatch) -> Callable[[Handler], list[httpx.Request]]:
    def install(handler: Handler) -> list[httpx.Request]:
        seen: list[httpx.Request] = []

        def record(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return handler(request)

        transport = httpx.MockTransport(record)
        real_client, real_async = httpx.Client, httpx.AsyncClient
        for module in (search_module, fetcher_module):
            monkeypatch.setattr(module.httpx, "Client", lambda **kw: real_client(transport=transport, **kw))
            monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kw: real_async(transport=transport, **kw))
        return seen

    return install


KEY = Secret.from_token(API_KEY)


class TestWebSearch:
    def test_run(self, mock_http) -> None:
        seen = mock_http(lambda request: httpx.Response(200, json={"organic_results": ROWS}))
        result = PiloterrWebSearch(api_key=KEY, top_k=5, search_params={"gl": "us"}).run(query="haystack")

        assert seen[0].url.path == "/v2/google/search"
        assert dict(seen[0].url.params) == {"gl": "us", "query": "haystack", "num": "5"}
        assert seen[0].headers["x-api-key"] == API_KEY
        assert [d.content for d in result["documents"]] == ["LLM framework.", "Only a title"]
        assert result["links"] == ["https://haystack.deepset.ai/", "https://example.com/"]

    def test_domains_and_no_top_k(self, mock_http) -> None:
        seen = mock_http(lambda request: httpx.Response(200, json={}))
        result = PiloterrWebSearch(api_key=KEY, top_k=None, allowed_domains=["a.com"]).run(query="q")

        assert dict(seen[0].url.params) == {"query": "q site:a.com"}
        assert result == {"documents": [], "links": []}

    @pytest.mark.parametrize(
        ("status", "body", "expected"),
        [
            (401, {"error": "Invalid API key"}, "rejected the API key"),
            (401, {"error": {"message": "Rate limit exceeded"}}, "Piloterr rate limit exceeded (HTTP 401)"),
            (500, {"message": f"boom {API_KEY}"}, "Piloterr API error (HTTP 500): boom ***"),
        ],
    )
    def test_errors(self, mock_http, status, body, expected) -> None:
        mock_http(lambda request: httpx.Response(status, json=body))
        with pytest.raises(PiloterrError, match=re.escape(expected)) as info:
            PiloterrWebSearch(api_key=KEY).run(query="q")
        assert info.value.status_code == status

    async def test_run_async(self, mock_http) -> None:
        mock_http(lambda request: httpx.Response(200, json={"organic_results": ROWS}))
        assert len((await PiloterrWebSearch(api_key=KEY).run_async(query="q"))["documents"]) == 2

    def test_serialization(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PILOTERR_API_KEY", API_KEY)
        data = PiloterrWebSearch(top_k=3, search_params={"hl": "fr"}).to_dict()
        restored = PiloterrWebSearch.from_dict(data)
        assert (restored.top_k, restored.search_params) == (3, {"hl": "fr"})


class TestFetcher:
    def test_crawler_stream(self, mock_http) -> None:
        seen = mock_http(lambda request: httpx.Response(200, json=PAGE))
        streams = PiloterrFetcher(api_key=KEY).run(urls=["https://example.com"])["streams"]

        assert seen[0].url.path == "/v2/website/crawler"
        assert dict(seen[0].url.params) == {"query": "https://example.com"}
        assert streams[0].data == PAGE.encode()
        assert streams[0].mime_type == "text/html"
        assert streams[0].meta == {"url": "https://example.com", "content_type": "text/html", "engine": "crawler"}

    def test_rendering_wait_and_raw_body(self, mock_http) -> None:
        seen = mock_http(lambda request: httpx.Response(200, text=PAGE))
        streams = PiloterrFetcher(api_key=KEY, engine="rendering", wait_in_seconds=60).run(urls=["https://a.b"])[
            "streams"
        ]
        assert seen[0].url.path == "/v2/website/rendering"
        assert seen[0].url.params["wait_in_seconds"] == "30"
        assert streams[0].data == PAGE.encode()

    def test_single_failure_raises(self, mock_http) -> None:
        mock_http(lambda request: httpx.Response(402, json={"error": "No credits"}))
        with pytest.raises(PiloterrError, match="out of credits"):
            PiloterrFetcher(api_key=KEY).run(urls=["https://a.b"])

    def test_failures_are_skipped_with_several_urls(self, mock_http) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if "bad" in str(request.url):
                return httpx.Response(500, json={"error": "upstream"})
            return httpx.Response(200, json=PAGE)

        mock_http(handler)
        streams = PiloterrFetcher(api_key=KEY).run(urls=["https://bad.example", "ftp://x", "https://good.example"])[
            "streams"
        ]
        assert [s.meta["url"] for s in streams] == ["https://good.example"]

    def test_invalid_single_url(self, mock_http) -> None:
        seen = mock_http(lambda request: httpx.Response(200, json=PAGE))
        with pytest.raises(PiloterrError, match="absolute http"):
            PiloterrFetcher(api_key=KEY).run(urls=["example.com"])
        assert seen == []

    def test_timeout(self, mock_http) -> None:
        def slow(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow", request=request)

        mock_http(slow)
        with pytest.raises(PiloterrError, match="did not answer within 5 seconds"):
            PiloterrFetcher(api_key=KEY, timeout=5).run(urls=["https://a.b"])

    async def test_run_async(self, mock_http) -> None:
        mock_http(lambda request: httpx.Response(200, json=PAGE))
        streams = (await PiloterrFetcher(api_key=KEY, engine="webunlocker").run_async(urls=["https://a.b"]))["streams"]
        assert streams[0].meta["engine"] == "webunlocker"

    def test_unknown_engine(self) -> None:
        with pytest.raises(ValueError, match="engine"):
            PiloterrFetcher(api_key=KEY, engine="browser")

    def test_pipeline_with_html_converter(self, mock_http, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PILOTERR_API_KEY", API_KEY)
        mock_http(lambda request: httpx.Response(200, json=PAGE))
        pipeline = Pipeline()
        pipeline.add_component("fetcher", PiloterrFetcher())
        pipeline.add_component("converter", HTMLToDocument())
        pipeline.connect("fetcher.streams", "converter.sources")
        restored = Pipeline.loads(pipeline.dumps())
        docs = restored.run({"fetcher": {"urls": ["https://example.com"]}})["converter"]["documents"]
        assert "For use in examples." in docs[0].content
        assert docs[0].meta["url"] == "https://example.com"


live = pytest.mark.skipif(not os.environ.get("PILOTERR_API_KEY"), reason="PILOTERR_API_KEY is not set")


@pytest.mark.integration
@live
def test_live_search() -> None:
    assert PiloterrWebSearch(top_k=3).run(query="haystack llm framework")["links"]


@pytest.mark.integration
@live
def test_live_fetch() -> None:
    streams = PiloterrFetcher().run(urls=["https://example.com"])["streams"]
    assert b"Example Domain" in streams[0].data
