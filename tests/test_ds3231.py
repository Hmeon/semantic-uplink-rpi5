from __future__ import annotations

from datetime import datetime, timezone

import pytest

from edge.rtc.ds3231 import DS3231, RTCGuardian


class FakeBus:
    def __init__(self, read_data: list[int] | None = None) -> None:
        self.read_data = read_data or []
        self.writes: list[tuple[int, int, list[int]]] = []

    def read_i2c_block_data(self, addr: int, register: int, length: int) -> list[int]:
        return list(self.read_data)

    def write_i2c_block_data(self, addr: int, register: int, data: list[int]) -> None:
        self.writes.append((addr, register, list(data)))

    def close(self) -> None:  # pragma: no cover - nothing to close
        return None


class SequenceNow:
    def __init__(self, seq: list[datetime]):
        self._seq = list(seq)
        if not self._seq:
            raise ValueError("sequence must contain at least one datetime")
        self._idx = 0

    def __call__(self) -> datetime:
        if self._idx < len(self._seq):
            val = self._seq[self._idx]
            self._idx += 1
            return val
        return self._seq[-1]


class FakeRTC:
    def __init__(self, current: datetime) -> None:
        self.current = current
        self.write_calls: list[datetime] = []

    def read_time(self) -> datetime:
        return self.current

    def write_time(self, dt: datetime) -> None:
        self.current = dt
        self.write_calls.append(dt)


def test_ds3231_read_time_decodes_registers() -> None:
    # 2024-05-06 14:23:45 UTC (Monday)
    data = [0x45, 0x23, 0x14, 0x02, 0x06, 0x05, 0x24]
    rtc = DS3231(bus=1, bus_factory=lambda _: FakeBus(data))
    dt = rtc.read_time()
    assert dt == datetime(2024, 5, 6, 14, 23, 45, tzinfo=timezone.utc)


def test_ds3231_write_time_encodes_registers() -> None:
    bus = FakeBus()
    rtc = DS3231(bus=1, bus_factory=lambda _: bus)
    rtc.write_time(datetime(2031, 12, 31, 23, 59, 58, tzinfo=timezone.utc))
    assert bus.writes == [
        (0x68, 0x00, [0x58, 0x59, 0x23, 0x03, 0x31, 0x12, 0x31]),
    ]


def test_guardian_syncs_system_when_system_is_behind() -> None:
    rtc_time = datetime(2024, 1, 1, 0, 0, 10, tzinfo=timezone.utc)
    rtc = FakeRTC(rtc_time)
    now_seq = SequenceNow([
        datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
        datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
        datetime(2024, 1, 1, 0, 0, 10, tzinfo=timezone.utc),
    ])

    guardian = RTCGuardian(rtc, drift_guard_s=1.0, resync_interval_s=-1, time_provider=now_seq)

    called: dict[str, datetime] = {}

    def fake_sync_system(dt: datetime) -> None:
        called["ts"] = dt

    guardian._sync_system_from_rtc = fake_sync_system  # type: ignore[method-assign]

    status = guardian.guard_once()

    assert called["ts"] == rtc_time
    assert status.synced is True
    assert status.drift_seconds == pytest.approx(0.0, abs=1e-6)


def test_guardian_pushes_to_rtc_when_system_is_ahead() -> None:
    rtc_time = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    rtc = FakeRTC(rtc_time)
    now_seq = SequenceNow([
        datetime(2024, 1, 1, 0, 0, 10, tzinfo=timezone.utc),
        datetime(2024, 1, 1, 0, 0, 10, tzinfo=timezone.utc),
        datetime(2024, 1, 1, 0, 0, 10, tzinfo=timezone.utc),
    ])

    guardian = RTCGuardian(
        rtc,
        drift_guard_s=1.0,
        resync_interval_s=-1,
        push_system_to_rtc=True,
        time_provider=now_seq,
    )

    def fail_sync_system(_: datetime) -> None:
        raise AssertionError("system clock sync should not be called when pushing to RTC")

    guardian._sync_system_from_rtc = fail_sync_system  # type: ignore[method-assign]

    status = guardian.guard_once()

    assert rtc.write_calls == [datetime(2024, 1, 1, 0, 0, 10, tzinfo=timezone.utc)]
    assert status.synced is True
    assert status.drift_seconds == pytest.approx(0.0, abs=1e-6)
