from __future__ import annotations

from typing import Any

try:  # pragma: no cover - behavior is validated via public API tests
    import msgspec
except Exception:  # pragma: no cover
    msgspec = None  # type: ignore[assignment]

if msgspec is not None:
    _ENCODER = msgspec.json.Encoder()
    _DECODER = msgspec.json.Decoder()


def dumps(obj: Any) -> bytes:
    """
    Serialize an object to compact UTF-8 JSON bytes.

    - Prefers msgspec (fast) when installed.
    - Fallback uses stdlib json with no whitespace.
    """
    if msgspec is not None:
        return _ENCODER.encode(obj)

    import json

    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def loads(data: bytes) -> Any:
    """
    Parse UTF-8 JSON bytes into Python objects.

    - Prefers msgspec (fast) when installed.
    - Fallback decodes as UTF-8 and uses stdlib json.
    """
    if msgspec is not None:
        return _DECODER.decode(data)

    import json

    return json.loads(data.decode("utf-8"))

