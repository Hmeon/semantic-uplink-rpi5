from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from common.schema import EventMsg, LinkProfile, PolicyDecisionMsg, PolicyMode, SensorType
from edge import edge_daemon as daemon_mod
from edge.policy.runtime import StepResult


class _DummyStats:
    ack_latency_ewma_ms = 12.5
    loss_ewma = 0.2


class _DummyOutbox:
    def __init__(self, _path: str):
        self.closed = False
        self.enqueued = []
        self.pending_values = [0]

    def close(self) -> None:
        self.closed = True

    def pending(self) -> int:
        if len(self.pending_values) > 1:
            return int(self.pending_values.pop(0))
        return int(self.pending_values[0])

    def delivery_stats(self):
        return _DummyStats()

    def enqueue(self, topic, payload, qos, retain, created_ns):
        self.enqueued.append((topic, payload, qos, retain, created_ns))


class _DummyPublisher:
    def __init__(self, outbox, **_kwargs):
        self.outbox = outbox
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def is_connected(self) -> bool:
        return True


def _make_daemon(monkeypatch, tmp_path, *, mic=None, temp=None):
    monkeypatch.setattr(daemon_mod, "Outbox", _DummyOutbox)
    monkeypatch.setattr(daemon_mod, "MQTTPublisher", _DummyPublisher)
    return daemon_mod.EdgeDaemon(
        device_id="dev1",
        profile=LinkProfile.SLOW_10KBPS,
        outbox_path=str(tmp_path / "outbox.sqlite"),
        mode=PolicyMode.FIXED_TAU,
        mic=mic if mic is not None else daemon_mod.MicCfg(enable=True, backend="mock"),
        temp=temp if temp is not None else daemon_mod.TempCfg(enable=False),
        rtc=daemon_mod.RTCCfg(enable=False),
        ui=daemon_mod.UICfg(enable=False),
        buttons=daemon_mod.ButtonsCfg(enable=False),
        link=daemon_mod.LinkCfg(apply_on_button=False, apply_on_start=False),
    )


def _mk_event(*, sensor: SensorType, seq: int, val: float) -> EventMsg:
    ts = 1_700_000_000_000_000_000 + seq
    return EventMsg(
        ts=ts,
        seq=seq,
        device_id="dev1",
        sensor=sensor,
        val=val,
        pred=val - 0.1,
        res=0.1,
        tau=0.2,
        kbits=8,
        profile=LinkProfile.SLOW_10KBPS,
        policy=PolicyMode.FIXED_TAU,
    )


def _mk_decision(ts: int) -> PolicyDecisionMsg:
    return PolicyDecisionMsg(
        ts=ts,
        device_id="dev1",
        state_aoi=100.0,
        state_res=0.2,
        state_res_var=0.01,
        state_loss=0.0,
        state_q_len=0,
        tau=0.2,
        kbits=8,
        reward=0.5,
    )


@dataclass
class _PolicyCall:
    seq: int
    outbox_pending: int
    ack_ms: float | None
    loss_rate: float | None


def test_mic_loop_propagates_policy_step_and_honors_stop(monkeypatch, tmp_path) -> None:
    daemon = _make_daemon(monkeypatch, tmp_path)
    daemon.outbox.pending_values = [7, 9]

    built_cfg = {}

    calls: list[_PolicyCall] = []

    class _MicPolicy:
        def step(self, sample, *, outbox_pending, link_feedback):
            calls.append(
                _PolicyCall(
                    seq=int(sample.seq),
                    outbox_pending=int(outbox_pending),
                    ack_ms=link_feedback.ack_delay_ms,
                    loss_rate=link_feedback.loss_rate,
                )
            )
            event = _mk_event(
                sensor=SensorType.MIC_RMS,
                seq=int(sample.seq),
                val=float(sample.dbfs),
            )
            return StepResult(
                event=event,
                decision=_mk_decision(event.ts + 1),
                reward=1.0,
                aoi_ms=12.0,
                mae_est=0.5,
                rate_bps=64.0,
            )

    def _build_policy_runtime(**kwargs):
        built_cfg.update(kwargs)
        return _MicPolicy()

    monkeypatch.setattr(daemon, "_build_policy_runtime", _build_policy_runtime)

    created = {}

    class _FakeMic:
        def __init__(self, **_kwargs):
            self.closed = False
            created["obj"] = self

        def stream(self, duration_s=None):
            assert duration_s is None
            yield SimpleNamespace(seq=10, dbfs=-21.0, clip_ratio=0.03)
            yield SimpleNamespace(seq=11, dbfs=-20.0, clip_ratio=0.05)

        def close(self):
            self.closed = True

    monkeypatch.setattr(daemon_mod, "MicRMS", _FakeMic)

    mic_updates = []
    daemon._status.update_mic = lambda dbfs, clip: mic_updates.append((float(dbfs), float(clip)))  # type: ignore[method-assign]

    handled = []

    def _handle_step_result(res, *, label, sensor):
        handled.append((res, label, sensor))
        daemon._stop.set()

    monkeypatch.setattr(daemon, "_handle_step_result", _handle_step_result)
    daemon._stop.clear()
    daemon._mic_loop()

    assert built_cfg["sensor"] == SensorType.MIC_RMS
    assert built_cfg["nominal_period_s"] == daemon.mic_cfg.frame_ms / 1000.0
    assert mic_updates == [(-21.0, 0.03)]
    assert len(calls) == 1
    assert calls[0] == _PolicyCall(seq=10, outbox_pending=7, ack_ms=12.5, loss_rate=0.2)
    assert len(handled) == 1
    assert handled[0][1] == "mic"
    assert handled[0][2] == SensorType.MIC_RMS
    assert created["obj"].closed is True


def test_temp_loop_uses_stubbed_sensor_stream_and_policy(monkeypatch, tmp_path) -> None:
    daemon = _make_daemon(
        monkeypatch,
        tmp_path,
        mic=daemon_mod.MicCfg(enable=False),
        temp=daemon_mod.TempCfg(enable=True, backend="mock", sample_hz=2.0),
    )
    daemon.outbox.pending_values = [1, 4]

    built_cfg = {}
    calls: list[_PolicyCall] = []

    class _TempPolicy:
        def step(self, sample, *, outbox_pending, link_feedback):
            calls.append(
                _PolicyCall(
                    seq=int(sample.seq),
                    outbox_pending=int(outbox_pending),
                    ack_ms=link_feedback.ack_delay_ms,
                    loss_rate=link_feedback.loss_rate,
                )
            )
            event = None
            decision = None
            if sample.valid:
                event = _mk_event(
                    sensor=SensorType.TEMP,
                    seq=int(sample.seq),
                    val=float(sample.celsius),
                )
                decision = _mk_decision(event.ts + 1)
            return StepResult(
                event=event,
                decision=decision,
                reward=0.5,
                aoi_ms=30.0,
                mae_est=0.2,
                rate_bps=32.0,
            )

    def _build_policy_runtime(**kwargs):
        built_cfg.update(kwargs)
        return _TempPolicy()

    monkeypatch.setattr(daemon, "_build_policy_runtime", _build_policy_runtime)

    created = {}

    class _FakeTemp:
        def __init__(self, **_kwargs):
            self.closed = False
            created["obj"] = self

        def stream(self, duration_s=None):
            assert duration_s is None
            yield SimpleNamespace(seq=20, celsius=18.9, valid=False)
            yield SimpleNamespace(seq=21, celsius=19.3, valid=True)

        def close(self):
            self.closed = True

    monkeypatch.setattr(daemon_mod, "TempSensor", _FakeTemp)

    temp_updates = []
    daemon._status.update_temp = lambda c, valid: temp_updates.append((float(c), bool(valid)))  # type: ignore[method-assign]

    handled = []

    def _handle_step_result(res, *, label, sensor):
        handled.append((res, label, sensor))

    monkeypatch.setattr(daemon, "_handle_step_result", _handle_step_result)
    daemon._stop.clear()
    daemon._temp_loop()

    assert built_cfg["sensor"] == SensorType.TEMP
    assert built_cfg["nominal_period_s"] == 0.5
    assert temp_updates == [(18.9, False), (19.3, True)]
    assert calls == [
        _PolicyCall(seq=20, outbox_pending=1, ack_ms=12.5, loss_rate=0.2),
        _PolicyCall(seq=21, outbox_pending=4, ack_ms=12.5, loss_rate=0.2),
    ]
    assert len(handled) == 2
    assert all(label == "temp" for _, label, _ in handled)
    assert all(sensor == SensorType.TEMP for _, _, sensor in handled)
    assert handled[0][0].event is None
    assert handled[1][0].event is not None
    assert handled[1][0].decision is not None
    assert created["obj"].closed is True
