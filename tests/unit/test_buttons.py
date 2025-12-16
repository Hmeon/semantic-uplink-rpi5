from __future__ import annotations

import importlib
import sys
from types import ModuleType
from unittest.mock import MagicMock

import pytest


def _import_buttons_module(monkeypatch: pytest.MonkeyPatch, *, with_gpiozero: bool) -> ModuleType:
    sys.modules.pop("edge.ui.buttons", None)

    if with_gpiozero:
        gpiozero = ModuleType("gpiozero")
        gpiozero.Button = MagicMock(name="Button")  # type: ignore[attr-defined]

        gpiozero_exc = ModuleType("gpiozero.exc")
        gpiozero_exc.BadPinFactory = type("BadPinFactory", (Exception,), {})  # type: ignore[attr-defined]
        gpiozero_exc.PinFactoryFallback = type(  # type: ignore[attr-defined]
            "PinFactoryFallback",
            (Exception,),
            {},
        )

        monkeypatch.setitem(sys.modules, "gpiozero", gpiozero)
        monkeypatch.setitem(sys.modules, "gpiozero.exc", gpiozero_exc)
    else:
        monkeypatch.delitem(sys.modules, "gpiozero", raising=False)
        monkeypatch.delitem(sys.modules, "gpiozero.exc", raising=False)

    return importlib.import_module("edge.ui.buttons")


def test_buttons_config_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    m = _import_buttons_module(monkeypatch, with_gpiozero=False)
    cfg = m.ButtonsConfig()
    assert cfg.enable is True
    assert cfg.mode_pin == 17
    assert cfg.debounce_ms == 200


def test_build_buttons_returns_dummy_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    m = _import_buttons_module(monkeypatch, with_gpiozero=True)
    cfg = m.ButtonsConfig(enable=False)
    btn = m.build_buttons(cfg, lambda: None, lambda: None, lambda: None)
    assert getattr(btn, "reason", "") == "disabled by config"


def test_build_buttons_returns_dummy_when_gpiozero_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    m = _import_buttons_module(monkeypatch, with_gpiozero=False)
    cfg = m.ButtonsConfig(enable=True)
    btn = m.build_buttons(cfg, lambda: None, lambda: None, lambda: None)
    assert "gpiozero not installed" in getattr(btn, "reason", "")


def test_buttons_start_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    m = _import_buttons_module(monkeypatch, with_gpiozero=True)

    btn_mode = MagicMock()
    btn_profile = MagicMock()
    btn_marker = MagicMock()
    m.Button.side_effect = [btn_mode, btn_profile, btn_marker]

    cfg = m.ButtonsConfig(enable=True, debounce_ms=100)
    on_mode = MagicMock()
    on_profile = MagicMock()
    on_marker = MagicMock()

    buttons = m.Buttons(cfg, on_mode, on_profile, on_marker)
    buttons.start()

    assert m.Button.call_count == 3
    m.Button.assert_any_call(cfg.mode_pin, pull_up=True, bounce_time=0.1)

    buttons.stop()
    btn_mode.close.assert_called_once()
    btn_profile.close.assert_called_once()
    btn_marker.close.assert_called_once()


def test_buttons_callback_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    m = _import_buttons_module(monkeypatch, with_gpiozero=True)

    btn_mode = MagicMock()
    btn_profile = MagicMock()
    btn_marker = MagicMock()
    m.Button.side_effect = [btn_mode, btn_profile, btn_marker]

    on_mode = MagicMock()
    buttons = m.Buttons(m.ButtonsConfig(), on_mode, MagicMock(), MagicMock())
    buttons.start()

    assert btn_mode.when_pressed is not None
    btn_mode.when_pressed(None)
    on_mode.assert_called_once()
