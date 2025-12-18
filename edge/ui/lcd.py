"""I2C LCD/OLED 상태 표시."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Sequence

from edge.ui.status import UISnapshot

logger = logging.getLogger(__name__)

try:  # pragma: no cover - 하드웨어 의존
    from smbus2 import SMBus  # type: ignore
except Exception:  # pragma: no cover - smbus 미설치 환경
    SMBus = None  # type: ignore

__all__ = ["DisplayConfig", "DisplayInitError", "build_display"]


@dataclass(slots=True)
class DisplayConfig:
    kind: str = "auto"      # auto|lcd1602|ssd1306|console
    bus: int = 1
    address: int | None = None
    refresh_s: float = 1.0


class DisplayInitError(RuntimeError):
    pass


class BaseDisplay:
    columns: int
    rows: int

    def show_lines(self, lines: Sequence[str]) -> None:
        raise NotImplementedError

    def show_snapshot(self, snap: UISnapshot) -> None:
        lines = format_lines(snap, self.columns, self.rows)
        self.show_lines(lines)

    def close(self) -> None:
        pass


class ConsoleDisplay(BaseDisplay):
    """하드웨어 없을 때 기본 출력."""
    columns = 32
    rows = 4

    def __init__(self):
        self._last = None

    def show_lines(self, lines: Sequence[str]) -> None:
        block = " | ".join(line.rstrip() for line in lines)
        if block != self._last:
            logger.info("ui %s", block)
            self._last = block


class PCF8574LCD(BaseDisplay):  # pragma: no cover - 하드웨어 스코프
    """HD44780 + PCF8574 I2C(1602)"""
    columns = 16
    rows = 2

    def __init__(self, bus: int = 1, address: int = 0x27, backlight: bool = True):
        if SMBus is None:
            raise DisplayInitError("smbus2 is required for LCD1602")
        self.bus_no = int(bus)
        self.address = int(address)
        self._backlight = 0x08 if backlight else 0x00
        self._bus = SMBus(self.bus_no)
        self._init_display()

    def _init_display(self) -> None:
        # 4-bit 초기화 시퀀스
        for cmd in (0x33, 0x32, 0x06, 0x0C, 0x28, 0x01):
            self._write_cmd(cmd)
            time.sleep(0.005)

    def _write_cmd(self, cmd: int) -> None:
        self._write_byte(cmd, mode=0)

    def _write_data(self, data: int) -> None:
        self._write_byte(data, mode=1)

    def _write_byte(self, bits: int, mode: int) -> None:
        high = mode | (bits & 0xF0) | self._backlight
        low = mode | ((bits << 4) & 0xF0) | self._backlight
        self._toggle_enable(high)
        self._toggle_enable(low)

    def _toggle_enable(self, data: int) -> None:
        enable = 0x04
        self._bus.write_byte(self.address, data | enable)
        time.sleep(0.0005)
        self._bus.write_byte(self.address, data & ~enable)
        time.sleep(0.0001)

    def show_lines(self, lines: Sequence[str]) -> None:
        padded = _normalize_lines(lines, self.columns, self.rows)
        # 라인 주소: 0x80, 0xC0
        for idx, line in enumerate(padded[: self.rows]):
            self._write_cmd(0x80 | (0x40 * idx))
            for ch in line:
                self._write_data(ord(ch))

    def close(self) -> None:
        try:
            self._write_cmd(0x01)
        except Exception:
            pass
        try:
            self._bus.close()
        except Exception:
            pass


class SSD1306Display(BaseDisplay):  # pragma: no cover - 하드웨어 스코프
    """luma.oled 기반 0.96\" SSD1306."""
    columns = 21
    rows = 4

    def __init__(self, bus: int = 1, address: int = 0x3C):
        try:
            from luma.core.interface.serial import i2c
            from luma.oled.device import ssd1306
            from luma.core.render import canvas
            from PIL import ImageFont
        except Exception as e:
            raise DisplayInitError("Install luma.oled + pillow for SSD1306 support") from e
        self._canvas_ctx = canvas
        serial = i2c(port=int(bus), address=int(address))
        self._device = ssd1306(serial, width=128, height=64)
        self._font = ImageFont.load_default()

    def show_lines(self, lines: Sequence[str]) -> None:
        padded = _normalize_lines(lines, self.columns, self.rows)
        with self._canvas_ctx(self._device) as draw:
            for i, line in enumerate(padded[: self.rows]):
                y = 2 + i * 14
                draw.text((0, y), line, font=self._font, fill=255)

    def close(self) -> None:
        try:
            self._device.hide()
        except Exception:
            pass


def _normalize_lines(lines: Sequence[str], columns: int, rows: int) -> list[str]:
    normed = []
    for line in list(lines)[:rows]:
        s = line[:columns]
        if len(s) < columns:
            s = s.ljust(columns)
        normed.append(s)
    while len(normed) < rows:
        normed.append(" " * columns)
    return normed


def _abbr_mode(mode: str) -> str:
    m = mode.lower()
    if "periodic" in m:
        return "PER"
    if "adaptive" in m:
        return "LIN"
    if "fixed" in m or "tau" in m:
        return "ETS"
    return mode[:3].upper()


def _abbr_profile(profile: str) -> str:
    if profile.startswith("slow"):
        return "S10K"
    if profile.startswith("delay"):
        return "DELAY"
    if profile.startswith("cell"):
        return "CELL"
    return profile[:5].upper()


def format_lines(snap: UISnapshot, columns: int, rows: int) -> list[str]:
    temp = "--.-" if snap.temp_c is None else f"{snap.temp_c:4.1f}"
    mic = "--.-" if snap.mic_dbfs is None else f"{snap.mic_dbfs:5.1f}"
    clip = "" if snap.mic_clip is None else f"{snap.mic_clip * 100:02.0f}%"
    rate_kbps = snap.tx_rate_bps / 1000.0
    link = "UP" if snap.mqtt_connected else "DN"
    q = f"Q{snap.outbox_pending}"
    profile = snap.profile.value
    mode = snap.mode.value
    aoi_txt = "--" if snap.aoi_ms is None else f"{snap.aoi_ms:4.0f}"
    mae_txt = "--" if snap.mae is None else f"{snap.mae:3.1f}"

    lines: list[str] = []
    mode_abbr = _abbr_mode(mode)
    prof_abbr = _abbr_profile(profile)
    if columns <= 16 and rows <= 2:
        # 16x2 LCD용 압축 포맷
        lines.append(f"{mode_abbr}/{prof_abbr} R{rate_kbps:4.1f}k")
        lines.append(f"A{aoi_txt} M{mae_txt} {link} {q}")
    else:
        lines.append(f"{mode_abbr}/{prof_abbr} T:{temp}C M:{mic}")
        clip_txt = f"clip {clip}" if clip else "clip --"
        lines.append(f"{clip_txt} {link} {q}")
        lines.append(f"R:{rate_kbps:5.1f} kbps AoI:{aoi_txt}")
        lines.append(f"MAE:{mae_txt}")
    return _normalize_lines(lines, columns, rows)


def build_display(cfg: DisplayConfig) -> BaseDisplay:
    order = [cfg.kind] if cfg.kind != "auto" else ["lcd1602", "ssd1306", "console"]
    last_err: Exception | None = None
    for kind in order:
        try:
            if kind == "lcd1602":
                addresses = [cfg.address] if cfg.address is not None else [0x27, 0x3F]
                for addr in addresses:
                    try:
                        return PCF8574LCD(bus=cfg.bus, address=addr)
                    except Exception:
                        continue
                raise DisplayInitError("LCD1602 not found on known addresses")
            if kind == "ssd1306":
                return SSD1306Display(bus=cfg.bus, address=cfg.address or 0x3C)
            if kind == "console":
                return ConsoleDisplay()
        except Exception as e:  # keep last error for context
            last_err = e
            continue
    if last_err:
        raise last_err
    raise DisplayInitError("no usable display backend")
