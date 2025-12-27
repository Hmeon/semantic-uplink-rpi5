"""Time utilities (ISO-8601 helpers and AoI helper)."""

from __future__ import annotations

from datetime import datetime, timezone

ISO_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"


def now_iso() -> str:
    """Return current UTC time formatted as ISO-8601.

    Args:
        None.

    Returns:
        Timestamp string in ISO-8601 format with UTC suffix.

    Raises:
        None.

    Side Effects:
        - Reads system clock.

    Contract:
        - Uses UTC timezone for formatting.

    Failure Modes:
        - None.
    """
    return datetime.now(timezone.utc).strftime(ISO_FMT)


def iso_to_epoch(ts: str) -> float:
    """Parse an ISO-8601 timestamp into epoch seconds.

    Args:
        ts: ISO-8601 timestamp string.

    Returns:
        Seconds since Unix epoch (UTC).

    Raises:
        ValueError: If the timestamp cannot be parsed.

    Side Effects:
        - None.

    Contract:
        - Accepts "YYYY-MM-DDTHH:MM:SSZ" and "YYYY-MM-DDTHH:MM:SS.sssZ".
        - Accepts datetime.fromisoformat variants (e.g., "+00:00").

    Failure Modes:
        - Invalid formats raise ValueError.
    """
    s = str(ts).strip()
    try:
        if s.endswith("Z"):
            # datetime.fromisoformat does not accept trailing "Z".
            return datetime.fromisoformat(s[:-1] + "+00:00").timestamp()
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        pass

    for fmt in (ISO_FMT, "%Y-%m-%dT%H:%M:%SZ"):
        try:
            dt = datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except Exception:
            continue

    raise ValueError(f"invalid ISO-8601 timestamp: {ts!r}")


def aoi_ms(now_epoch: float, gen_epoch: float) -> float:
    """Compute AoI in milliseconds from two epoch timestamps.

    Args:
        now_epoch: Current time in epoch seconds.
        gen_epoch: Generation time in epoch seconds.

    Returns:
        Non-negative AoI in milliseconds.

    Raises:
        None.

    Side Effects:
        - None.

    Contract:
        - Negative deltas are clamped to zero.

    Failure Modes:
        - Non-finite inputs yield NaN results via float arithmetic.
    """
    return max(0.0, (float(now_epoch) - float(gen_epoch)) * 1000.0)
