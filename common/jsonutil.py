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
    """Serialize an object to compact UTF-8 JSON bytes.

    Args:
        obj: JSON-serializable object.

    Returns:
        UTF-8 encoded JSON bytes with no extra whitespace.

    Raises:
        TypeError: If the object cannot be serialized by the backend.
        ValueError: If the backend rejects the payload.

    Side Effects:
        - None.

    Contract:
        - Prefers msgspec when installed, otherwise uses stdlib json.

    Failure Modes:
        - Serialization errors propagate to the caller.
    """
    if msgspec is not None:
        return _ENCODER.encode(obj)

    import json

    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def loads(data: bytes) -> Any:
    """Parse UTF-8 JSON bytes into Python objects.

    Args:
        data: UTF-8 encoded JSON bytes.

    Returns:
        Parsed Python object (dict/list/str/etc).

    Raises:
        ValueError: If the JSON payload is invalid.
        TypeError: If the input is not bytes-like.

    Side Effects:
        - None.

    Contract:
        - Prefers msgspec when installed, otherwise uses stdlib json.

    Failure Modes:
        - Decode errors propagate to the caller.
    """
    if msgspec is not None:
        return _DECODER.decode(data)

    import json

    return json.loads(data.decode("utf-8"))
