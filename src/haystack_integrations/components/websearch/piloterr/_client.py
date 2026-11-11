"""HTTP plumbing shared by the Piloterr components."""

from __future__ import annotations

from typing import Any

import httpx
from haystack.core.errors import ComponentError

BASE_URL = "https://api.piloterr.com"
USER_AGENT = "piloterr-haystack"
_ERROR_DETAIL_LIMIT = 300


class PiloterrError(ComponentError):
    """A Piloterr request failed. ``status_code`` is None for a timeout or a network failure."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _error(response: httpx.Response, api_key: str) -> PiloterrError:
    detail = ""
    try:
        body = response.json()
    except ValueError:
        detail = response.text.strip()
    else:
        if isinstance(body, dict):
            error = body.get("error") or body.get("message") or ""
            if isinstance(error, dict):
                error = error.get("message") or ""
            detail = str(error)
    if api_key:
        detail = detail.replace(api_key, "***")
    detail = detail[:_ERROR_DETAIL_LIMIT]
    status = response.status_code
    if status == 401 and "rate limit" in detail.lower():
        label = "Piloterr rate limit exceeded"
    elif status == 401:
        label = "Piloterr rejected the API key, check PILOTERR_API_KEY"
    elif status == 402:
        label = "Piloterr account is out of credits"
    elif status == 429:
        label = "Piloterr rate limit exceeded"
    else:
        label = "Piloterr API error"
    return PiloterrError(f"{label} (HTTP {status})" + (f": {detail}" if detail else ""), status)


def headers(api_key: str) -> dict[str, str]:
    return {"x-api-key": api_key, "Accept": "application/json", "User-Agent": USER_AGENT}


def check(response: httpx.Response, api_key: str) -> httpx.Response:
    if not response.is_success:
        raise _error(response, api_key)
    return response


def transport_error(exc: httpx.HTTPError, api_key: str, timeout: float) -> PiloterrError:
    if isinstance(exc, httpx.TimeoutException):
        return PiloterrError(f"Piloterr did not answer within {timeout:g} seconds.")
    return PiloterrError(f"Could not reach Piloterr: {str(exc).replace(api_key, '***') if api_key else exc}")


def page_html(response: httpx.Response) -> str:
    """The page HTML of a website endpoint: a JSON string, or a raw HTML body."""
    try:
        body: Any = response.json()
    except ValueError:
        return response.text
    if not isinstance(body, str):
        raise PiloterrError("Piloterr returned an unexpected response shape; expected the page HTML.")
    return body
