# edge/ui/buttons.py
# 목적: GPIO 버튼 3개(모드/프로파일/마커) 처리. RPi.GPIO 없으면 안전 폴백.

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Optional

try:  # pragma: no cover - 하드웨어 환경에서만 의미 있음
    import RPi.GPIO as GPIO  # type: ignore
except Exception:  # pragma: no cover
    GPIO = None  # type: ignore

__all__ = ["ButtonsConfig", "Buttons", "ButtonsInitError"]


class ButtonsInitError(RuntimeError):
    pass


@dataclass(slots=True)
class ButtonsConfig:
    enable: bool = True
    mode_pin: int = 17
    profile_pin: int = 27
    marker_pin: int = 22
    debounce_ms: int = 200


class _DummyButtons:
    def __init__(self, reason: str = "GPIO unavailable"):
        self.reason = reason

    def start(self) -> None:
        print(f"[buttons] disabled: {self.reason}")

    def stop(self) -> None:
        return


class Buttons:  # pragma: no cover - 하드웨어 스코프
    def __init__(
        self,
        cfg: ButtonsConfig,
        on_mode: Callable[[], None],
        on_profile: Callable[[], None],
        on_marker: Callable[[], None],
    ):
        if GPIO is None:
            raise ButtonsInitError("RPi.GPIO not available")
        self.cfg = cfg
        self._on_mode = on_mode
        self._on_profile = on_profile
        self._on_marker = on_marker
        self._started = False
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            GPIO.setmode(GPIO.BCM)
            for pin in (self.cfg.mode_pin, self.cfg.profile_pin, self.cfg.marker_pin):
                GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            GPIO.add_event_detect(
                self.cfg.mode_pin,
                GPIO.FALLING,
                callback=self._wrap(self._on_mode),
                bouncetime=self.cfg.debounce_ms,
            )
            GPIO.add_event_detect(
                self.cfg.profile_pin,
                GPIO.FALLING,
                callback=self._wrap(self._on_profile),
                bouncetime=self.cfg.debounce_ms,
            )
            GPIO.add_event_detect(
                self.cfg.marker_pin,
                GPIO.FALLING,
                callback=self._wrap(self._on_marker),
                bouncetime=self.cfg.debounce_ms,
            )
            self._started = True
            print("[buttons] started (mode/profile/marker)")

    def stop(self) -> None:
        with self._lock:
            if not self._started:
                return
            for pin in (self.cfg.mode_pin, self.cfg.profile_pin, self.cfg.marker_pin):
                try:
                    GPIO.remove_event_detect(pin)
                except Exception:
                    pass
            try:
                GPIO.cleanup()
            except Exception:
                pass
            self._started = False
            print("[buttons] stopped")

    def _wrap(self, fn: Callable[[], None]):
        def _cb(channel: Optional[int] = None):
            try:
                fn()
            except Exception as e:
                # 콜백에서 예외가 나도 GPIO 쓰레드를 죽이지 않도록 로그만.
                print(f"[buttons] handler error: {e}")
        return _cb


def build_buttons(
    cfg: ButtonsConfig,
    on_mode: Callable[[], None],
    on_profile: Callable[[], None],
    on_marker: Callable[[], None],
):
    if not cfg.enable:
        return _DummyButtons("disabled by config")
    try:
        return Buttons(cfg, on_mode, on_profile, on_marker)
    except ButtonsInitError as e:
        return _DummyButtons(str(e))
