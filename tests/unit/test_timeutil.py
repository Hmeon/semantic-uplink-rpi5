from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest

from common.timeutil import ISO_FMT, aoi_ms, iso_to_epoch, now_iso


def test_now_iso_uses_expected_utc_format() -> None:
    ts = now_iso()
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z", ts)
    # Round-trip parse sanity via declared format.
    parsed = datetime.strptime(ts, ISO_FMT).replace(tzinfo=timezone.utc)
    assert parsed.tzinfo == timezone.utc


def test_iso_to_epoch_accepts_z_and_offset_variants() -> None:
    s_z = "2026-01-31T00:00:00Z"
    s_off = "2026-01-31T00:00:00+00:00"
    assert iso_to_epoch(s_z) == pytest.approx(iso_to_epoch(s_off))

    s_z_frac = "2026-01-31T00:00:00.123456Z"
    s_off_frac = "2026-01-31T00:00:00.123456+00:00"
    assert iso_to_epoch(s_z_frac) == pytest.approx(iso_to_epoch(s_off_frac))


def test_iso_to_epoch_rejects_invalid_input() -> None:
    with pytest.raises(ValueError):
        iso_to_epoch("not-a-timestamp")


def test_aoi_ms_is_non_negative() -> None:
    assert aoi_ms(10.0, 9.5) == pytest.approx(500.0)
    assert aoi_ms(9.0, 10.0) == pytest.approx(0.0)

