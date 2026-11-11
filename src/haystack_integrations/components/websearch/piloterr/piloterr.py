"""Piloterr Google search component for Haystack."""

from __future__ import annotations

from typing import Any

import httpx
from haystack import component, default_from_dict, default_to_dict
from haystack.dataclasses import Document
from haystack.utils import Secret, deserialize_secrets_inplace

from haystack_integrations.components.websearch.piloterr import _client
from haystack_integrations.components.websearch.piloterr._client import PiloterrError


@component
class PiloterrWebSearch:
    """
    Searches Google with Piloterr.

    It returns one `Document` per organic result, with the snippet as content and the `title`, `link` and
    `position` in its metadata, plus the list of result links.

    ### Usage example

    ```python
    from haystack_integrations.components.websearch.piloterr import PiloterrWebSearch

    search = PiloterrWebSearch(top_k=5)  # reads PILOTERR_API_KEY
    result = search.run(query="What is Haystack?")
    ```

    A failed request raises `PiloterrError`, a Haystack `ComponentError` with the HTTP status. The API key never
    appears in the message.
    """

    def __init__(
        self,
        api_key: Secret = Secret.from_env_var("PILOTERR_API_KEY"),  # noqa: B008 (Haystack convention)
        top_k: int | None = 10,
        allowed_domains: list[str] | None = None,
        search_params: dict[str, Any] | None = None,
        timeout: float = 60.0,
    ) -> None:
        """
        Initialize the component.

        :param api_key: Piloterr API key. Defaults to the `PILOTERR_API_KEY` environment variable.
            Get a key at https://app.piloterr.com
        :param top_k: Number of results requested from Google (`num`, 1 to 100); `None` uses Google's default.
        :param allowed_domains: Only search these domains, added to the query as `site:` filters.
        :param search_params: Extra query parameters, such as `{"gl": "us", "hl": "en-gb", "location": "Paris"}`.
            See https://docs.piloterr.com
        :param timeout: Seconds to wait for Piloterr.
        """
        self.api_key = api_key
        self.top_k = top_k
        self.allowed_domains = allowed_domains
        self.search_params = search_params or {}
        self.timeout = timeout
        self.api_key.resolve_value()

    def to_dict(self) -> dict[str, Any]:
        """Serialize the component to a dictionary."""
        return default_to_dict(
            self,
            api_key=self.api_key.to_dict(),
            top_k=self.top_k,
            allowed_domains=self.allowed_domains,
            search_params=self.search_params,
            timeout=self.timeout,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PiloterrWebSearch:
        """Deserialize the component from a dictionary."""
        deserialize_secrets_inplace(data["init_parameters"], keys=["api_key"])
        return default_from_dict(cls, data)

    def _request(self, query: str) -> tuple[str, dict[str, Any], str]:
        api_key = (self.api_key.resolve_value() or "").strip()
        if not api_key:
            raise PiloterrError("Piloterr API key is missing; set PILOTERR_API_KEY.")
        if self.allowed_domains:
            sites = " OR ".join(f"site:{domain}" for domain in self.allowed_domains)
            query = f"{query} ({sites})" if len(self.allowed_domains) > 1 else f"{query} {sites}"
        params = {**self.search_params, "query": query}
        if self.top_k is not None:
            params["num"] = min(max(self.top_k, 1), 100)
        return f"{_client.BASE_URL}/v2/google/search", params, api_key

    def _parse(self, response: httpx.Response, api_key: str) -> dict[str, Any]:
        _client.check(response, api_key)
        try:
            body = response.json()
        except ValueError as exc:
            raise PiloterrError("Piloterr returned a response that is not JSON.") from exc
        rows = body.get("organic_results") if isinstance(body, dict) else None
        if isinstance(body, dict) and rows is None:
            rows = []
        if not isinstance(rows, list):
            raise PiloterrError("Piloterr returned an unexpected response shape.")
        documents = []
        for row in rows:
            if isinstance(row, dict):
                meta = {key: row[key] for key in ("title", "link", "position") if row.get(key)}
                documents.append(Document(content=str(row.get("snippet") or row.get("title") or ""), meta=meta))
        if self.top_k is not None:
            documents = documents[: self.top_k]
        return {"documents": documents, "links": [d.meta["link"] for d in documents if d.meta.get("link")]}

    @component.output_types(documents=list[Document], links=list[str])
    def run(self, query: str) -> dict[str, Any]:
        """
        Search Google.

        :param query: The search query.
        :returns: A dictionary with `documents` (one per result) and `links` (their URLs).
        :raises PiloterrError: If the request fails.
        """
        url, params, api_key = self._request(query)
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(url, params=params, headers=_client.headers(api_key))
        except httpx.HTTPError as exc:
            raise _client.transport_error(exc, api_key, self.timeout) from exc
        return self._parse(response, api_key)

    @component.output_types(documents=list[Document], links=list[str])
    async def run_async(self, query: str) -> dict[str, Any]:
        """Async version of `run`."""
        url, params, api_key = self._request(query)
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(url, params=params, headers=_client.headers(api_key))
        except httpx.HTTPError as exc:
            raise _client.transport_error(exc, api_key, self.timeout) from exc
        return self._parse(response, api_key)
