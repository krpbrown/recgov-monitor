from urllib.error import URLError

import pytest

from recgov_monitor.http import HttpClient


def test_get_json_wraps_url_errors_as_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise_url_error(*_args, **_kwargs):
        raise URLError("temporary failure in name resolution")

    monkeypatch.setattr("recgov_monitor.http.request.urlopen", _raise_url_error)
    client = HttpClient(timeout_seconds=1)

    with pytest.raises(RuntimeError, match="GET request failed"):
        client.get_json("https://example.com/api")


def test_post_json_wraps_url_errors_as_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise_url_error(*_args, **_kwargs):
        raise URLError("temporary failure in name resolution")

    monkeypatch.setattr("recgov_monitor.http.request.urlopen", _raise_url_error)
    client = HttpClient(timeout_seconds=1)

    with pytest.raises(RuntimeError, match="Webhook request failed"):
        client.post_json("https://example.com/webhook", {"content": "hello"})
