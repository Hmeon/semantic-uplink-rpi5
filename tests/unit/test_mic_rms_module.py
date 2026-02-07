from __future__ import annotations

import numpy as np
import pytest

from edge.sensors import mic_rms as mic_mod


class _FrameSource:
    def __init__(self, frames):
        self._frames = list(frames)
        self.closed = False

    def read_frame(self):
        if not self._frames:
            return None
        return self._frames.pop(0)

    def close(self) -> None:
        self.closed = True


def test_mic_auto_backend_prefers_sounddevice_when_available(monkeypatch) -> None:
    src = _FrameSource([None])
    monkeypatch.setattr(mic_mod, "_has_sounddevice", lambda: True)
    monkeypatch.setattr(
        mic_mod,
        "_SoundDeviceSource",
        lambda *_args, **_kwargs: src,
    )
    monkeypatch.setattr(
        mic_mod,
        "_ARecordSource",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("must not call arecord")),
    )

    mic = mic_mod.MicRMS(device_id="dev1", backend="auto")
    assert mic._backend_name == "sounddevice"
    mic.close()


def test_mic_auto_backend_falls_back_to_arecord_when_sounddevice_unavailable(monkeypatch) -> None:
    src = _FrameSource([None])
    monkeypatch.setattr(mic_mod, "_has_sounddevice", lambda: False)
    monkeypatch.setattr(
        mic_mod,
        "_ARecordSource",
        lambda *_args, **_kwargs: src,
    )

    mic = mic_mod.MicRMS(device_id="dev1", backend="auto")
    assert mic._backend_name == "arecord"
    mic.close()


def test_mic_stream_computes_dbfs_and_clip_ratio(monkeypatch) -> None:
    frame = np.array([0, 32767, -32768, 0], dtype=np.int16)
    src = _FrameSource([frame, None])
    monkeypatch.setattr(
        mic_mod,
        "_ARecordSource",
        lambda *_args, **_kwargs: src,
    )

    mic = mic_mod.MicRMS(device_id="dev1", backend="arecord", clip_threshold=0.999)
    it = mic.stream(duration_s=None)
    sample = next(it)
    with pytest.raises(StopIteration):
        next(it)
    mic.close()

    assert sample.seq == 0
    assert -3.2 < sample.dbfs < -2.8
    assert sample.clip_ratio == 0.5


def test_mic_stream_returns_no_samples_when_duration_is_zero(monkeypatch) -> None:
    src = _FrameSource([np.array([1, 2, 3, 4], dtype=np.int16)])
    monkeypatch.setattr(
        mic_mod,
        "_ARecordSource",
        lambda *_args, **_kwargs: src,
    )

    mic = mic_mod.MicRMS(device_id="dev1", backend="arecord")
    assert list(mic.stream(duration_s=0.0)) == []
    mic.close()


def test_mic_init_validation_and_close_error_swallow(monkeypatch) -> None:
    with pytest.raises(ValueError):
        mic_mod.MicRMS(device_id="dev1", backend="arecord", clip_threshold=1.1)
    with pytest.raises(ValueError):
        mic_mod.MicRMS(device_id="dev1", backend="arecord", sample_rate=0)

    class _BadClose(_FrameSource):
        def close(self) -> None:
            raise RuntimeError("close failed")

    bad = _BadClose([None])
    monkeypatch.setattr(
        mic_mod,
        "_ARecordSource",
        lambda *_args, **_kwargs: bad,
    )
    mic = mic_mod.MicRMS(device_id="dev1", backend="arecord")
    mic.close()
