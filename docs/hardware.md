# Raspberry Pi 5 Hardware Wiring (Semantic Uplink)

This document captures the fixed wiring used by the RPi5 semantic uplink platform. Keep this in sync with any pin/address changes.

## ASCII Block Diagram
```
          +---------------------------+
          | Raspberry Pi 5 (8GB)      |
          |  - I2C1 SDA/SCL           |
          |  - GPIO 4  (1-Wire)       |
          |  - GPIO 17/27/22 (Btns)   |
          |  - GPIO 18 (Buzzer opt)   |
          |  - USB (Mic+Soundcard)    |
          +---------------------------+
             | I2C bus 1 @0x27
             |--------------------+
             |                    |
        +---------+          +---------+
        | PCF8574 |          | 1602 LCD|
        +---------+          +---------+
             |
             +-----(SDA/SCL/5V/GND)

  1-Wire (GPIO4) ---- DS18B20 ---- 3V3/GND

  GPIO17 ----[Button MODE ]---- GND (pull-up)
  GPIO27 ----[Button LINK ]---- GND (pull-up)
  GPIO22 ----[Button RUN  ]---- GND (pull-up)

  GPIO18 ----[Buzzer optional]---- GND (active high)

  USB ---- USB Mic + USB Soundcard (16 kHz, 100 ms RMS)
```

## Pin Mapping (BCM)
| Signal / Function | Physical Pin | BCM # | Notes |
| --- | --- | --- | --- |
| I2C1 SDA | 3 | 2 | LCD PCF8574 SDA |
| I2C1 SCL | 5 | 3 | LCD PCF8574 SCL |
| LCD VCC | 2 or 4 | — | 5V |
| LCD GND | 6 | — | Common ground |
| DS18B20 Data | 7 | 4 | 4.7k pull-up to 3V3 |
| DS18B20 VCC | 1 or 17 | — | 3V3 |
| DS18B20 GND | 9 | — | Ground |
| Button MODE | 11 | 17 | Internal pull-up, to GND |
| Button LINK | 13 | 27 | Internal pull-up, to GND |
| Button RUN/Marker | 15 | 22 | Internal pull-up, to GND |
| Buzzer (opt) | 12 | 18 | Active high to GND |
| USB Mic/Soundcard | USB-A | — | Audio input |

## I2C Addresses
- PCF8574 LCD backpack: default `0x27` (configurable; fallback `0x3F`).
- DS3231 RTC (optional): `0x68` when enabled.

## Notes
- 1-Wire: enable with `dtoverlay=w1-gpio,gpiopin=4` on Raspberry Pi OS.
- I2C: enable `i2c_arm` and verify `/dev/i2c-1`.
- Buttons use internal pull-ups; wiring is active-low to ground.
- If you change any pin or address, update this file and the corresponding CLI defaults in `edge/edge_daemon.py`.
- For repeatable mic measurements, keep capture gain fixed and disable AGC across all runs.
