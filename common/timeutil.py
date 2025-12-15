"""Time utilities (ISO-8601 helpers and AoI helper)."""

from __future__ import annotations

from datetime import datetime, timezone

ISO_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime(ISO_FMT)


def iso_to_epoch(ts: str) -> float:
    """
    Parse an ISO-8601 timestamp into epoch seconds.

    Accepts:
    - "YYYY-MM-DDTHH:MM:SSZ"
    - "YYYY-MM-DDTHH:MM:SS.sssZ"
    - Python's `datetime.fromisoformat` variants (e.g., "+00:00")
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
    return max(0.0, (float(now_epoch) - float(gen_epoch)) * 1000.0)

