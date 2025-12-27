# edge/ui/buttons.py
# Purpose: Handle 3 GPIO buttons (Mode/Profile/Marker).
# Refactored for RPi 5 compatibility using gpiozero (works on RPi 4/5).

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from typing import Callable, Optional

def _load_gpiozero():
    if os.environ.get("PYTEST_CURRENT_TEST") and "gpiozero" not in sys.modules:
        return None, None, None
    try:
        from gpiozero import Button
        from gpiozero.exc import BadPinFactory, PinFactoryFallback
        return Button, BadPinFactory, PinFactoryFallback
    except ImportError:
        return None, None, None


Button, BadPinFactory, PinFactoryFallback = _load_gpiozero()

__all__ = ["ButtonsConfig", "Buttons", "ButtonsInitError"]

logger = logging.getLogger(__name__)


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
        logger.info("buttons_disabled reason=%s", self.reason)

    def stop(self) -> None:
        return


class Buttons:
    def __init__(
        self,
        cfg: ButtonsConfig,
        on_mode: Callable[[], None],
        on_profile: Callable[[], None],
        on_marker: Callable[[], None],
    ):
        if Button is None:
            raise ButtonsInitError("gpiozero library not installed")

        self.cfg = cfg
        self._on_mode = on_mode
        self._on_profile = on_profile
        self._on_marker = on_marker

        # Hold references to Button objects
        self._btn_mode: Optional[Button] = None
        self._btn_profile: Optional[Button] = None
        self._btn_marker: Optional[Button] = None

    def start(self) -> None:
        if self._btn_mode is not None:
            return  # Already started

        try:
            # bounce_time in seconds for gpiozero
            bounce = self.cfg.debounce_ms / 1000.0

            self._btn_mode = Button(self.cfg.mode_pin, pull_up=True, bounce_time=bounce)
            self._btn_mode.when_pressed = self._wrap(self._on_mode)

            self._btn_profile = Button(self.cfg.profile_pin, pull_up=True, bounce_time=bounce)
            self._btn_profile.when_pressed = self._wrap(self._on_profile)

            self._btn_marker = Button(self.cfg.marker_pin, pull_up=True, bounce_time=bounce)
            self._btn_marker.when_pressed = self._wrap(self._on_marker)

            logger.info("buttons_started backend=gpiozero")

        except (BadPinFactory, PinFactoryFallback, OSError) as e:
            # This might happen if running on non-Pi hardware or without permissions
            self.stop()  # Cleanup any partially created buttons
            raise ButtonsInitError(f"Failed to initialize GPIO: {e}")

    def stop(self) -> None:
        for btn in (self._btn_mode, self._btn_profile, self._btn_marker):
            if btn is not None:
                btn.close()
        self._btn_mode = None
        self._btn_profile = None
        self._btn_marker = None
        logger.info("buttons_stopped")

    def _wrap(self, fn: Callable[[], None]):
        def _cb(btn):
            try:
                fn()
            except Exception:
                logger.exception("buttons_handler_error")
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
        # Check if we can import gpiozero (handled at top level but good to double check safety)
        if Button is None:
            return _DummyButtons("gpiozero not installed")

        return Buttons(cfg, on_mode, on_profile, on_marker)
    except ButtonsInitError as e:
        return _DummyButtons(str(e))
