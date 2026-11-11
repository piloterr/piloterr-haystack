"""Piloterr web page fetcher for Haystack."""

from __future__ import annotations

import re
from typing import Any, Literal

import httpx
from haystack import component, default_from_dict, default_to_dict, logging
from haystack.dataclasses import ByteStream
from haystack.utils import Secret, deserialize_secrets_inplace

from haystack_integrations.components.websearch.piloterr import _client
from haystack_integrations.components.websearch.piloterr._client import PiloterrError

logger = logging.getLogger(__name__)

ENGINES = ("crawler", "rendering", "webunlocker")


@component
class PiloterrFetcher:
    """
    Fetches web pages through Piloterr and returns their HTML as `ByteStream`s.

    Piloterr downloads each page on its own infrastructure: with a plain crawler (`engine="crawler"`), a headless
    browser for JavaScript pages (`engine="rendering"`), or a web unlocker for pages behind anti-bot protection
    (`engine="webunlocker"`). Connect the output to `HTMLToDocument` to get documents, the same way as
    Haystack's `LinkContentFetcher`.

    ### Usage example

    ```python
    from haystack import Pipeline
    from haystack.components.converters import HTMLToDocument
    from haystack_integrations.components.fetchers.piloterr import PiloterrFetcher

    pipeline = Pipeline()
    pipeline.add_component("fetcher", PiloterrFetcher(engine="rendering"))
    pipeline.add_component("converter", HTMLToDocument())
    pipeline.connect("fetcher.streams", "converter.sources")
    docs = pipeline.run({"fetcher": {"urls": ["https://example.com"]}})["converter"]["documents"]
    ```
    """

    def __init__(
        self,
        api_key: Secret = Secret.from_env_var("PILOTERR_API_KEY"),  # noqa: B008 (Haystack convention)
        engine: Literal["crawler", "rendering", "webunlocker"] = "crawler",
        wait_in_seconds: int | None = None,
        raise_on_failure: bool = True,
        timeout: float = 120.0,
    ) -> None:
        """
        Initialize the component.

        :param api_key: Piloterr API key. Defaults to the `PILOTERR_API_KEY` environment variable.
        :param engine: `crawler` (default), `rendering` (JavaScript pages) or `webunlocker` (protected pages).
        :param wait_in_seconds: Rendering only: extra seconds to wait after the page loads, 0 to 30.
        :param raise_on_failure: With one URL, raise on failure. With several URLs, failures are always logged
            and skipped so the other pages still come through, as `LinkContentFetcher` does.
        :param timeout: Seconds to wait for each page.
        """
        if engine not in ENGINES:
            raise ValueError(f"engine must be one of {', '.join(ENGINES)}")
        self.api_key = api_key
        self.engine = engine
        self.wait_in_seconds = wait_in_seconds
        self.raise_on_failure = raise_on_failure
        self.timeout = timeout
        self.api_key.resolve_value()

    def to_dict(self) -> dict[str, Any]:
        """Serialize the component to a dictionary."""
        return default_to_dict(
            self,
            api_key=self.api_key.to_dict(),
            engine=self.engine,
            wait_in_seconds=self.wait_in_seconds,
            raise_on_failure=self.raise_on_failure,
            timeout=self.timeout,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PiloterrFetcher:
        """Deserialize the component from a dictionary."""
        deserialize_secrets_inplace(data["init_parameters"], keys=["api_key"])
        return default_from_dict(cls, data)

    def _key(self) -> str:
        api_key = (self.api_key.resolve_value() or "").strip()
        if not api_key:
            raise PiloterrError("Piloterr API key is missing; set PILOTERR_API_KEY.")
        return api_key

    def _params(self, url: str) -> dict[str, Any]:
        url = url.strip()
        if not re.match(r"^https?://[^/\s]+", url, re.IGNORECASE):
            raise PiloterrError(f"Use an absolute http(s) URL, such as https://example.com; got {url!r}.")
        params: dict[str, Any] = {"query": url}
        if self.engine == "rendering" and self.wait_in_seconds:
            params["wait_in_seconds"] = min(max(self.wait_in_seconds, 0), 30)
        return params

    def _stream(self, url: str, html: str) -> ByteStream:
        return ByteStream(
            data=html.encode("utf-8"),
            meta={"url": url, "content_type": "text/html", "engine": self.engine},
            mime_type="text/html",
        )

    def _handle(self, error: PiloterrError, url: str, single: bool) -> None:
        if single and self.raise_on_failure:
            raise error
        logger.warning("Piloterr could not fetch {url}: {error}", url=url, error=str(error))

    @component.output_types(streams=list[ByteStream])
    def run(self, urls: list[str]) -> dict[str, list[ByteStream]]:
        """
        Fetch the pages.

        :param urls: The http(s) URLs to fetch.
        :returns: A dictionary with `streams`, one `ByteStream` of HTML per page that was fetched.
        :raises PiloterrError: With a single URL and `raise_on_failure=True`, if the request fails.
        """
        streams = []
        api_key = self._key()
        endpoint = f"{_client.BASE_URL}/v2/website/{self.engine}"
        with httpx.Client(timeout=self.timeout) as client:
            for url in urls:
                try:
                    params = self._params(url)
                    try:
                        response = client.get(endpoint, params=params, headers=_client.headers(api_key))
                    except httpx.HTTPError as exc:
                        raise _client.transport_error(exc, api_key, self.timeout) from exc
                    html = _client.page_html(_client.check(response, api_key))
                    streams.append(self._stream(params["query"], html))
                except PiloterrError as error:
                    self._handle(error, url, single=len(urls) == 1)
        return {"streams": streams}

    @component.output_types(streams=list[ByteStream])
    async def run_async(self, urls: list[str]) -> dict[str, list[ByteStream]]:
        """Async version of `run`."""
        streams = []
        api_key = self._key()
        endpoint = f"{_client.BASE_URL}/v2/website/{self.engine}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for url in urls:
                try:
                    params = self._params(url)
                    try:
                        response = await client.get(endpoint, params=params, headers=_client.headers(api_key))
                    except httpx.HTTPError as exc:
                        raise _client.transport_error(exc, api_key, self.timeout) from exc
                    html = _client.page_html(_client.check(response, api_key))
                    streams.append(self._stream(params["query"], html))
                except PiloterrError as error:
                    self._handle(error, url, single=len(urls) == 1)
        return {"streams": streams}
