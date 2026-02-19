from recbot2.notifier import explain_webhook_error, validate_discord_webhook_url


def test_validate_discord_webhook_url_accepts_valid_url() -> None:
    validate_discord_webhook_url(
        "https://discord.com/api/webhooks/123456789012345678/abcdef"
    )


def test_validate_discord_webhook_url_rejects_non_webhook_path() -> None:
    try:
        validate_discord_webhook_url("https://discord.com/channels/123/456")
    except ValueError as exc:
        assert "/api/webhooks/" in str(exc)
    else:
        raise AssertionError("Expected ValueError for non-webhook URL path")


def test_explain_webhook_error_for_403_1010() -> None:
    message = "Webhook request failed: 403 error code: 1010"
    explained = explain_webhook_error(message)
    assert "403/1010" in explained
    assert "valid webhook endpoint" in explained
