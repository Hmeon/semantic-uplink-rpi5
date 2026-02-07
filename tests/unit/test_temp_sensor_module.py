from __future__ import annotations

from dataclasses import dataclass

from edge.sensors import temp as temp_mod


class _SeqSource:
    source_name = "fake"

    def __init__(self, values):
        self._values = iter(values)
        self.raw_path = "fake://temp"
        self.closed = False

    def read_celsius(self):
        return next(self._values)

    def close(self) -> None:
        self.closed = True


def test_temp_auto_backend_falls_back_to_mock_when_hardware_unavailable(monkeypatch) -> None:
    def _raise_w1(*_args, **_kwargs):
        raise FileNotFoundError("no w1")

    def _raise_sysfs(*_args, **_kwargs):
        raise FileNotFoundError("no sysfs")

    monkeypatch.setattr(temp_mod, "_W1Source", _raise_w1)
    monkeypatch.setattr(temp_mod, "_SysfsSource", _raise_sysfs)

    sensor = temp_mod.TempSensor(device_id="dev1", backend="auto", sample_hz=10.0)
    try:
        assert sensor._backend_name == "mock"
    finally:
        sensor.close()


def test_temp_stream_reuses_last_value_when_read_fails(monkeypatch) -> None:
    src = _SeqSource([(25.0, True), (None, False)])
    monkeypatch.setattr(temp_mod.TempSensor, "_select_backend", lambda *_args, **_kwargs: src)

    sensor = temp_mod.TempSensor(device_id="dev1", backend="mock", sample_hz=1_000_000.0)
    g = sensor.stream(duration_s=None)
    first = next(g)
    second = next(g)
    g.close()
    sensor.close()

    assert first.valid is True
    assert first.celsius == 25.0
    assert second.valid is False
    assert second.celsius == 25.0
    assert second.seq == first.seq + 1


def test_temp_stream_marks_out_of_range_invalid_and_keeps_last_value(monkeypatch) -> None:
    src = _SeqSource([(22.0, True), (99.0, True)])
    monkeypatch.setattr(temp_mod.TempSensor, "_select_backend", lambda *_args, **_kwargs: src)

    sensor = temp_mod.TempSensor(
        device_id="dev1",
        backend="mock",
        sample_hz=1_000_000.0,
        min_c=0.0,
        max_c=50.0,
    )
    g = sensor.stream(duration_s=None)
    first = next(g)
    second = next(g)
    g.close()
    sensor.close()

    assert first.valid is True
    assert first.celsius == 22.0
    assert second.valid is False
    assert second.celsius == 22.0


def test_w1_and_sysfs_source_parsing_with_explicit_paths(tmp_path) -> None:
    w1 = tmp_path / "w1_slave"
    w1.write_text(
        "aa bb cc dd ee ff gg hh ii : crc=00 YES\n"
        "aa bb cc dd ee ff gg hh ii t=21562\n",
        encoding="ascii",
    )
    w1_src = temp_mod._W1Source(w1_path=str(w1))
    c, ok = w1_src.read_celsius()
    assert ok is True
    assert c == 21.562

    sysfs_milli = tmp_path / "temp_milli"
    sysfs_milli.write_text("43750\n", encoding="ascii")
    sys_src_milli = temp_mod._SysfsSource(sysfs_path=str(sysfs_milli))
    c_milli, ok_milli = sys_src_milli.read_celsius()
    assert ok_milli is True
    assert c_milli == 43.75

    sysfs_c = tmp_path / "temp_c"
    sysfs_c.write_text("41.5\n", encoding="ascii")
    sys_src_c = temp_mod._SysfsSource(sysfs_path=str(sysfs_c))
    c_plain, ok_plain = sys_src_c.read_celsius()
    assert ok_plain is True
    assert c_plain == 41.5


def test_temp_close_swallows_source_close_error(monkeypatch) -> None:
    @dataclass
    class _BadClose:
        source_name: str = "bad"
        raw_path: str | None = "fake://bad"

        def read_celsius(self):
            return (20.0, True)

        def close(self) -> None:
            raise RuntimeError("close failed")

    monkeypatch.setattr(
        temp_mod.TempSensor,
        "_select_backend",
        lambda *_args, **_kwargs: _BadClose(),
    )
    sensor = temp_mod.TempSensor(device_id="dev1", backend="mock", sample_hz=2.0)
    sensor.close()
