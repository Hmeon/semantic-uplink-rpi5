"""DS3231 RTC integration for Raspberry Pi 5 edge runtime.

The goal is to keep the system clock monotonic and precise even when the
network (and therefore NTP) is unavailable.  The DS3231 hardware clock is
treated as the authoritative time source on boot.  We periodically compare
the system clock against the RTC and, if the drift is above the configured
threshold, step the system clock so that AoI/timestamp metrics remain
trustworthy.

Design goals
============
* Fail gracefully when the I²C device is missing (e.g. when developing on a
  laptop).  The guardian simply becomes a no-op and logs a warning.
* Make the low level driver easy to unit test by injecting a fake SMBus
  implementation.
* Prefer ``hwclock --hctosys`` when available because it also takes care of
  the kernel↔RTC synchronisation details.  Fall back to Python's
  ``time.clock_settime`` when permissions allow.
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Protocol

try:  # pragma: no cover - imported lazily, guarded in tests
    from smbus2 import SMBus  # type: ignore
except Exception:  # pragma: no cover - smbus2 not installed on CI
    SMBus = None  # type: ignore[assignment]

_LOG = logging.getLogger(__name__)


class SMBusLike(Protocol):
    """Subset of ``smbus2.SMBus`` used by :class:`DS3231`."""

    def read_i2c_block_data(self, addr: int, register: int, length: int) -> list[int]:
        ...

    def write_i2c_block_data(self, addr: int, register: int, data: list[int]) -> None:
        ...

    def close(self) -> None:  # pragma: no cover - interface only
        ...


def _bcd_to_int(val: int) -> int:
    return ((val >> 4) * 10) + (val & 0x0F)


def _int_to_bcd(val: int) -> int:
    if not 0 <= val <= 99:
        raise ValueError(f"BCD value out of range: {val}")
    return ((val // 10) << 4) | (val % 10)


class DS3231:
    """Minimal driver to read/write time from a DS3231 RTC over I²C."""

    def __init__(
        self,
        *,
        bus: int = 1,
        address: int = 0x68,
        bus_factory: Callable[[int], SMBusLike] | None = None,
    ) -> None:
        if SMBus is None and bus_factory is None:
            raise RuntimeError("smbus2 is not available; install smbus2 to use the DS3231 driver")
        self.bus_num = bus
        self.address = address
        self._bus_factory = bus_factory or (lambda bus_num: SMBus(bus_num))  # type: ignore[arg-type]
        self._bus: SMBusLike | None = None

    # -- context manager helpers -------------------------------------------------
    def __enter__(self) -> "DS3231":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def open(self) -> None:
        if self._bus is None:
            self._bus = self._bus_factory(self.bus_num)

    def close(self) -> None:
        if self._bus is not None:
            try:
                self._bus.close()
            finally:
                self._bus = None

    # -- public API --------------------------------------------------------------
    def read_time(self) -> datetime:
        """Return the RTC time as an aware ``datetime`` in UTC."""

        self.open()
        assert self._bus is not None  # mypy
        data = self._bus.read_i2c_block_data(self.address, 0x00, 7)
        if len(data) != 7:
            raise RuntimeError(f"unexpected DS3231 response length: {len(data)}")

        sec = _bcd_to_int(data[0] & 0x7F)
        minute = _bcd_to_int(data[1] & 0x7F)
        hour_reg = data[2]
        if hour_reg & 0x40:
            # 12-hour mode; convert to 24h
            hour = _bcd_to_int(hour_reg & 0x1F)
            if hour_reg & 0x20:  # PM bit
                hour = (hour % 12) + 12
        else:
            hour = _bcd_to_int(hour_reg & 0x3F)

        date = _bcd_to_int(data[4] & 0x3F)
        month_reg = data[5]
        month = _bcd_to_int(month_reg & 0x1F)
        century = 2000 if (month_reg & 0x80) == 0 else 2100
        year = century + _bcd_to_int(data[6])

        return datetime(year, month, date, hour, minute, sec, tzinfo=timezone.utc)

    def write_time(self, dt: datetime) -> None:
        """Write a UTC datetime value into the RTC."""

        if dt.tzinfo is None:
            raise ValueError("datetime must be timezone-aware (UTC)")
        dt_utc = dt.astimezone(timezone.utc)
        payload = [
            _int_to_bcd(dt_utc.second),
            _int_to_bcd(dt_utc.minute),
            _int_to_bcd(dt_utc.hour),
            _int_to_bcd(dt_utc.isoweekday()),
            _int_to_bcd(dt_utc.day),
            _int_to_bcd(dt_utc.month) | (0x80 if dt_utc.year >= 2100 else 0x00),
            _int_to_bcd(dt_utc.year % 100),
        ]

        self.open()
        assert self._bus is not None
        self._bus.write_i2c_block_data(self.address, 0x00, payload)


@dataclass(slots=True)
class RTCStatus:
    """Snapshot of the guardian's view of the clocks."""

    rtc_time: datetime | None
    system_time: datetime
    drift_seconds: float | None
    synced: bool
    last_error: str | None = None


class RTCGuardian:
    """Background thread keeping the system clock in sync with the DS3231."""

    def __init__(
        self,
        rtc: DS3231,
        *,
        drift_guard_s: float = 2.0,
        resync_interval_s: float = 900.0,
        hwclock_path: str = "hwclock",
        push_system_to_rtc: bool = False,
        time_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self._rtc = rtc
        self.drift_guard_s = float(drift_guard_s)
        self.resync_interval_s = float(resync_interval_s)
        self.hwclock_path = hwclock_path
        self.push_system_to_rtc = push_system_to_rtc
        self._now = time_provider or (lambda: datetime.now(timezone.utc))

        self._status = RTCStatus(
            rtc_time=None,
            system_time=self._now(),
            drift_seconds=None,
            synced=False,
        )
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def status(self) -> RTCStatus:
        return self._status

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="rtc-guardian", daemon=True)
        self._thread.start()

    def stop(self, timeout: float | None = 2.0) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    # -- worker -----------------------------------------------------------------
    def _run(self) -> None:  # pragma: no cover - tiny wrapper around guard loop
        _LOG.info(
            "RTC guardian started (drift_guard_s=%.2f, resync_interval_s=%.1f)",
            self.drift_guard_s,
            self.resync_interval_s,
        )
        try:
            while not self._stop.is_set():
                self.guard_once()
                # resync_interval_s <= 0 disables the periodic loop but we still run once
                if self.resync_interval_s <= 0:
                    break
                self._stop.wait(self.resync_interval_s)
        finally:
            _LOG.info("RTC guardian stopped")

    # -- core -------------------------------------------------------------------
    def guard_once(self) -> RTCStatus:
        try:
            rtc_time = self._rtc.read_time()
        except Exception as exc:
            msg = f"RTC read failed: {exc}"
            _LOG.warning(msg)
            self._status = RTCStatus(
                rtc_time=None,
                system_time=self._now(),
                drift_seconds=None,
                synced=False,
                last_error=str(exc),
            )
            return self._status

        system_time = self._now()
        drift = abs((system_time - rtc_time).total_seconds())
        synced = drift <= self.drift_guard_s
        if not synced:
            if system_time < rtc_time or not self.push_system_to_rtc:
                self._sync_system_from_rtc(rtc_time)
            else:
                self._sync_rtc_from_system(system_time)
                rtc_time = system_time
            system_time = self._now()
            drift = abs((system_time - rtc_time).total_seconds())
            synced = drift <= self.drift_guard_s
        self._status = RTCStatus(
            rtc_time=rtc_time,
            system_time=system_time,
            drift_seconds=drift,
            synced=synced,
        )
        return self._status

    # -- helpers ----------------------------------------------------------------
    def _sync_system_from_rtc(self, rtc_time: datetime) -> None:
        epoch = rtc_time.timestamp()
        try:
            time.clock_settime(time.CLOCK_REALTIME, epoch)
            _LOG.info("System clock stepped to RTC time (clock_settime)")
            return
        except (PermissionError, AttributeError, OSError):
            pass

        try:
            subprocess.run([self.hwclock_path, "--hctosys", "--utc"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            _LOG.info("System clock stepped to RTC time via hwclock")
        except FileNotFoundError:
            _LOG.error("hwclock not found and clock_settime failed; cannot sync system clock from RTC")
        except subprocess.CalledProcessError as exc:
            _LOG.error("hwclock failed (rc=%s): %s", exc.returncode, exc.stderr.decode(errors="ignore"))

    def _sync_rtc_from_system(self, system_time: datetime) -> None:
        try:
            self._rtc.write_time(system_time)
            _LOG.info("RTC updated from system clock")
            return
        except Exception as exc:
            _LOG.error("Failed to write RTC: %s", exc)

        try:
            subprocess.run([self.hwclock_path, "--systohc", "--utc"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            _LOG.info("hwclock --systohc completed")
        except FileNotFoundError:
            _LOG.debug("hwclock not available for systohc")
        except subprocess.CalledProcessError as exc:
            _LOG.error("hwclock --systohc failed (rc=%s): %s", exc.returncode, exc.stderr.decode(errors="ignore"))

