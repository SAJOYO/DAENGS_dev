"""Small provider requests without httpx's URL log (query strings contain service keys)."""

import json

import httpx


class PublicSourceError(Exception):
    def __init__(self, reason, retryable=False):
        super().__init__(reason)
        self.reason, self.retryable = reason, retryable


async def get_json(transport, url, params, *, limit=2_000_000):
    # Use the public transport interface directly: no client INFO log containing the URL.
    request = httpx.Request(
        "GET",
        url,
        params=params,
        extensions={
            "timeout": {"connect": 3.0, "read": 5.0, "write": 3.0, "pool": 3.0},
        },
    )
    try:
        response = await transport.handle_async_request(request)
        try:
            code = response.status_code
            if code != 200:
                raise PublicSourceError(f"http_{code}", code == 429 or code >= 500)
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > limit:
                    raise PublicSourceError("response_too_large")
            return json.loads(data)
        finally:
            await response.aclose()
    except httpx.RequestError:
        raise PublicSourceError("transport_error", True) from None
    except (ValueError, TypeError):
        raise PublicSourceError("invalid_response") from None
