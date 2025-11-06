"""Discord webhook client utilities.

This module provides a small helper around Discord's webhook HTTP API.  It
keeps the dependency footprint minimal by relying only on the standard library
(`urllib.request`) while still exposing a typed, friendly surface for the rest
of the project.  The helper performs input validation, serialises payloads as
JSON, and raises rich exceptions when Discord returns an error response.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, MutableMapping

__all__ = ["DiscordWebhookError", "WebhookPayload", "send_discord_message"]


class DiscordWebhookError(RuntimeError):
    """Raised when the Discord webhook request fails."""


@dataclass(slots=True)
class WebhookPayload:
    """A strongly-typed representation of a Discord webhook payload."""

    content: str
    username: str | None = None
    embeds: Iterable[Mapping[str, Any]] | None = None
    allowed_mentions: Mapping[str, Any] | None = None

    def to_dict(self) -> MutableMapping[str, Any]:
        body: MutableMapping[str, Any] = {"content": self.content}
        if self.username:
            body["username"] = self.username
        if self.embeds:
            embeds_list = list(self.embeds)
            if embeds_list:
                body["embeds"] = embeds_list
        if self.allowed_mentions:
            body["allowed_mentions"] = dict(self.allowed_mentions)
        return body


def _encode_payload(payload: WebhookPayload) -> bytes:
    data = json.dumps(payload.to_dict(), ensure_ascii=False).encode("utf-8")
    return data


def send_discord_message(
    webhook_url: str,
    content: str,
    *,
    username: str | None = None,
    embeds: Iterable[Mapping[str, Any]] | None = None,
    allowed_mentions: Mapping[str, Any] | None = None,
    timeout: float = 10.0,
) -> None:
    """Send a message to a Discord webhook.

    Parameters
    ----------
    webhook_url:
        The HTTPS webhook endpoint provided by Discord.
    content:
        The plain-text content to post.  Discord limits this to 2000
        characters; the caller is expected to respect that limit.
    username:
        Optional override for the webhook username.
    embeds:
        Optional iterable of embed dictionaries; these must already conform to
        Discord's embed schema.
    allowed_mentions:
        Optional allowed mentions configuration.
    timeout:
        Socket timeout in seconds for the HTTP request.
    """

    if not webhook_url:
        raise ValueError("webhook_url must not be empty")
    if not webhook_url.startswith("https://"):
        raise ValueError("Discord webhook URL must use HTTPS")
    payload = WebhookPayload(
        content=content,
        username=username,
        embeds=embeds,
        allowed_mentions=allowed_mentions,
    )
    data = _encode_payload(payload)
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # type: ignore[arg-type]
            status = getattr(resp, "status", resp.getcode())
            if status < 200 or status >= 300:
                body = resp.read().decode("utf-8", errors="replace")
                raise DiscordWebhookError(
                    f"Discord webhook responded with status={status}: {body}"
                )
    except urllib.error.HTTPError as exc:  # pragma: no cover - exercised in tests
        body = exc.read().decode("utf-8", errors="replace")
        raise DiscordWebhookError(
            f"Discord webhook failed with status={exc.code}: {body}"
        ) from exc
    except urllib.error.URLError as exc:  # pragma: no cover - exercised in tests
        raise DiscordWebhookError(f"Discord webhook request error: {exc.reason}") from exc
    except socket.timeout as exc:  # pragma: no cover
        raise DiscordWebhookError("Discord webhook request timed out") from exc

