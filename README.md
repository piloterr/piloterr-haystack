# piloterr-haystack

[Haystack](https://haystack.deepset.ai) integration for [Piloterr](https://www.piloterr.com), a web data API.

It adds two components:

- `PiloterrWebSearch` searches Google and returns the results as Haystack `Document`s.
- `PiloterrFetcher` fetches web pages through Piloterr and returns their HTML as `ByteStream`s, like Haystack's `LinkContentFetcher`.

Piloterr downloads each page on its own infrastructure, in one of three ways:
- `crawler`: plain HTTP, the default;
- `rendering`: a headless browser, for JavaScript pages;
- `webunlocker`: for pages behind anti-bot protection.

## Installation

```bash
pip install piloterr-haystack
```

Get an API key at [app.piloterr.com](https://app.piloterr.com) and set it in the environment:

```bash
export PILOTERR_API_KEY="your-api-key"
```

## Web search

```python
from haystack_integrations.components.websearch.piloterr import PiloterrWebSearch

search = PiloterrWebSearch(top_k=5, search_params={"gl": "us", "hl": "en"})
result = search.run(query="What is Haystack?")
```

`run` returns two outputs:
- `documents`: one `Document` per organic result, with the snippet as content and `title`, `link` and `position` in the metadata;
- `links`: the result URLs.

| Parameter | Default | Description |
| --- | --- | --- |
| `api_key` | `Secret.from_env_var("PILOTERR_API_KEY")` | Piloterr API key |
| `top_k` | `10` | Results requested from Google, 1 to 100 |
| `allowed_domains` | | Only search these domains, added to the query as `site:` filters |
| `search_params` | | Extra parameters, such as `gl`, `hl` or `location` |
| `timeout` | `60` | Seconds to wait for Piloterr |

## Fetching pages

```python
from haystack import Pipeline
from haystack.components.converters import HTMLToDocument
from haystack_integrations.components.fetchers.piloterr import PiloterrFetcher

pipeline = Pipeline()
pipeline.add_component("fetcher", PiloterrFetcher(engine="rendering", wait_in_seconds=2))
pipeline.add_component("converter", HTMLToDocument())
pipeline.connect("fetcher.streams", "converter.sources")

result = pipeline.run({"fetcher": {"urls": ["https://example.com"]}})
print(result["converter"]["documents"][0].content)
```

| Parameter | Default | Description |
| --- | --- | --- |
| `api_key` | `Secret.from_env_var("PILOTERR_API_KEY")` | Piloterr API key |
| `engine` | `crawler` | `crawler`, `rendering` or `webunlocker` |
| `wait_in_seconds` | | Rendering only: extra wait after the page loads, 0 to 30 |
| `raise_on_failure` | `True` | Raise when a single URL fails; with several URLs, failures are logged and skipped |
| `timeout` | `120` | Seconds to wait for each page |

Each stream's metadata holds `url`, `content_type` and `engine`.

A failed request raises `PiloterrError`, a Haystack `ComponentError` with the HTTP status. The API key never appears in the message. Piloterr reports a rate limit as HTTP 401 with "Rate limit exceeded", and the error says so. Both components have `run_async` too.

## Development

```bash
pip install -e ".[test]"
pytest -m "not integration"     # offline
PILOTERR_API_KEY=... pytest      # with the live tests
```

## License

MIT
