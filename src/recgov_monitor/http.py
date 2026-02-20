from __future__ import annotations

import json
from urllib import parse, request
from urllib.error import HTTPError


class HttpClient:
    def __init__(self, timeout_seconds: int = 15) -> None:
        self.timeout_seconds = timeout_seconds
        self.default_headers = {
            "User-Agent": "recgov-monitor/1.0",
            "Accept": "application/json",
        }

    def get_json(self, url: str, params: dict[str, str] | None = None) -> dict:
        if params:
            url = f"{url}?{parse.urlencode(params)}"

        req = request.Request(url, method="GET", headers=self.default_headers)
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"GET request failed: {exc.code} {body}") from exc

    def post_json(self, url: str, payload: dict) -> None:
        data = json.dumps(payload).encode("utf-8")
        req = request.Request(
            url,
            data=data,
            method="POST",
            headers={
                **self.default_headers,
                "Content-Type": "application/json",
            },
        )
        try:
            with request.urlopen(req, timeout=self.timeout_seconds):
                return
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Webhook request failed: {exc.code} {body}") from exc
