from __future__ import annotations

import io
import json
import urllib.error

import pandas as pd
import pytest

from collector.analyze import format_summary_for_discord
from common import discord_webhook
from common.discord_webhook import DiscordWebhookError, send_discord_message


def test_send_discord_message_success(monkeypatch):
    captured: dict[str, object] = {}

    class DummyResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def getcode(self) -> int:
            return 204

        def read(self) -> bytes:
            return b""

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["data"] = req.data
        captured["headers"] = req.headers
        captured["timeout"] = timeout
        return DummyResponse()

    monkeypatch.setattr(discord_webhook.urllib.request, "urlopen", fake_urlopen)

    send_discord_message(
        "https://discord.test/api/webhooks/12345/abcdef",
        "hello world",
        username="bot",
        timeout=5.0,
    )

    assert captured["url"] == "https://discord.test/api/webhooks/12345/abcdef"
    assert captured["timeout"] == 5.0
    payload = json.loads(captured["data"].decode("utf-8"))
    assert payload["content"] == "hello world"
    assert payload["username"] == "bot"


def test_send_discord_message_http_error(monkeypatch):
    def fake_urlopen(req, timeout):  # noqa: ARG001 - signature required by urlopen
        raise urllib.error.HTTPError(
            req.full_url,
            400,
            "Bad Request",
            hdrs={},
            fp=io.BytesIO(b"bad"),
        )

    monkeypatch.setattr(discord_webhook.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(DiscordWebhookError):
        send_discord_message("https://discord.test/webhook", "oops")


def test_format_summary_for_discord_basic():
    df = pd.DataFrame(
        [
            {
                "profile": "slow_10kbps",
                "policy": "periodic",
                "sensor": "mic",
                "n_events": 42,
                "rate_Bps": 12.34,
                "aoi_mean_ms": 456.7,
                "aoi_p95_ms": 890.1,
                "mae_event_mean": 0.1234,
                "mae_event_p95": 0.4567,
                "kbits_mean": 6.78,
            },
            {
                "profile": "slow_10kbps",
                "policy": "fixed_tau",
                "sensor": "temp",
                "n_events": 12,
                "rate_Bps": float("nan"),
                "aoi_mean_ms": float("nan"),
                "aoi_p95_ms": 1234.5,
                "mae_event_mean": 0.2222,
                "mae_event_p95": 0.3333,
                "kbits_mean": 8.0,
            },
        ]
    )

    msg = format_summary_for_discord(df, limit=1)
    assert "**Semantic Uplink 분석 요약**" in msg
    assert "총 2행 중 상위 1행을 전송" in msg
    assert "`slow_10kbps/periodic`" in msg
    assert "rate=12.3 B/s" in msg
    assert "AoIμ=456.7 ms" in msg
    assert "MAE=0.123" in msg
    assert "… (총 2행 중 1행만 표시)" in msg


def test_format_summary_for_discord_empty():
    df = pd.DataFrame(columns=[
        "profile",
        "policy",
        "sensor",
        "n_events",
        "rate_Bps",
        "aoi_mean_ms",
        "aoi_p95_ms",
        "mae_event_mean",
        "mae_event_p95",
        "kbits_mean",
    ])

    msg = format_summary_for_discord(df)
    assert "집계된 행이 없어" in msg
