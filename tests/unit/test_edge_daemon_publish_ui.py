from __future__ import annotations

import json

from common.schema import EventMsg, LinkProfile, PolicyDecisionMsg, PolicyMode, SensorType
from edge import edge_daemon as daemon_mod
from edge.policy.runtime import StepResult


class _DummyStats:
    ack_latency_ewma_ms = 0.0
    loss_ewma = 0.0


class _DummyOutbox:
    def __init__(self, _path: str):
        self.closed = False
        self.enqueued = []
        self._pending = 0

    def close(self) -> None:
        self.closed = True

    def pending(self) -> int:
        return self._pending

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


def _make_daemon(monkeypatch, tmp_path):
    monkeypatch.setattr(daemon_mod, "Outbox", _DummyOutbox)
    monkeypatch.setattr(daemon_mod, "MQTTPublisher", _DummyPublisher)
    return daemon_mod.EdgeDaemon(
        device_id="dev1",
        profile=LinkProfile.SLOW_10KBPS,
        outbox_path=str(tmp_path / "outbox.sqlite"),
        mode=PolicyMode.FIXED_TAU,
        mic=daemon_mod.MicCfg(enable=False),
        temp=daemon_mod.TempCfg(enable=False),
        rtc=daemon_mod.RTCCfg(enable=False),
        ui=daemon_mod.UICfg(enable=False),
        buttons=daemon_mod.ButtonsCfg(enable=False),
        link=daemon_mod.LinkCfg(apply_on_button=False, apply_on_start=False),
    )


def _mk_event(seq: int) -> EventMsg:
    ts = 1_700_000_000_000_000_000 + seq
    return EventMsg(
        ts=ts,
        seq=seq,
        device_id="dev1",
        sensor=SensorType.TEMP,
        val=20.0,
        pred=19.8,
        res=0.2,
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


def _mk_step(
    *,
    event: EventMsg | None,
    decision: PolicyDecisionMsg | None,
    reward: float = 0.0,
    aoi_ms: float = 0.0,
    mae_est: float = 0.0,
    rate_bps: float = 0.0,
) -> StepResult:
    return StepResult(
        event=event,
        decision=decision,
        reward=reward,
        aoi_ms=aoi_ms,
        mae_est=mae_est,
        rate_bps=rate_bps,
    )


def test_ui_loop_continues_after_render_and_pending_failures(monkeypatch, tmp_path) -> None:
    daemon = _make_daemon(monkeypatch, tmp_path)
    daemon._stop.clear()

    class _Display:
        def __init__(self):
            self.calls = 0
            self.closed = False

        def show_snapshot(self, _snap):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("render failed once")

        def close(self):
            self.closed = True

    disp = _Display()
    daemon._display = disp

    calls = {"pending": 0}

    def _pending_once_fail():
        calls["pending"] += 1
        if calls["pending"] == 1:
            raise RuntimeError("pending failed once")
        return 3

    daemon.outbox.pending = _pending_once_fail
    daemon.publisher.is_connected = lambda: True

    def _sleep_and_stop(_seconds):
        if disp.calls >= 2:
            daemon._stop.set()

    monkeypatch.setattr(daemon_mod.time, "sleep", _sleep_and_stop)
    daemon._ui_loop()

    assert disp.calls >= 2
    assert disp.closed is True


def test_ui_loop_tracks_fluctuating_connectivity_and_backlog(monkeypatch, tmp_path) -> None:
    daemon = _make_daemon(monkeypatch, tmp_path)
    daemon._stop.clear()
    daemon._status.update_temp(23.1, True)
    daemon._status.update_mic(-15.2, 0.01)

    snapshots = []

    class _Display:
        def __init__(self):
            self.closed = False

        def show_snapshot(self, snap):
            snapshots.append(snap)
            if len(snapshots) >= 3:
                daemon._stop.set()

        def close(self):
            self.closed = True

    disp = _Display()
    daemon._display = disp

    pending_values = iter([0, 5, 2])
    daemon.outbox.pending = lambda: next(pending_values)

    mqtt_values = iter([True, False, True])
    daemon.publisher.is_connected = lambda: next(mqtt_values)

    monkeypatch.setattr(daemon_mod.time, "sleep", lambda _seconds: None)
    daemon._ui_loop()

    assert [snap.outbox_pending for snap in snapshots] == [0, 5, 2]
    assert [snap.mqtt_connected for snap in snapshots] == [True, False, True]
    assert all(snap.temp_c == 23.1 for snap in snapshots)
    assert all(snap.temp_valid is True for snap in snapshots)
    assert all(snap.mic_dbfs == -15.2 for snap in snapshots)
    assert all(snap.mic_clip == 0.01 for snap in snapshots)
    assert disp.closed is True


def test_should_publish_decision_mode_matrix(monkeypatch, tmp_path) -> None:
    daemon = _make_daemon(monkeypatch, tmp_path)
    event = _mk_event(1)
    decision = _mk_decision(event.ts + 1)

    daemon.decision_cfg.publish = "never"
    assert daemon._should_publish_decision(_mk_step(event=event, decision=decision)) is False

    daemon.decision_cfg.publish = "event"
    assert daemon._should_publish_decision(_mk_step(event=None, decision=decision)) is False
    assert daemon._should_publish_decision(_mk_step(event=event, decision=decision)) is True

    daemon.decision_cfg.publish = "always"
    assert daemon._should_publish_decision(_mk_step(event=None, decision=decision)) is True


def test_handle_step_result_decision_publish_gate(monkeypatch, tmp_path) -> None:
    daemon = _make_daemon(monkeypatch, tmp_path)
    event = _mk_event(2)
    decision = _mk_decision(event.ts + 1)

    daemon.decision_cfg.publish = "never"
    daemon._handle_step_result(
        _mk_step(
            event=event,
            decision=decision,
            reward=1.0,
            aoi_ms=10.0,
            mae_est=0.1,
            rate_bps=100.0,
        ),
        label="temp",
        sensor=SensorType.TEMP,
    )
    topics_never = [t for (t, *_rest) in daemon.outbox.enqueued]
    assert any(t.startswith("edge/dev1/temp/") for t in topics_never)
    assert not any(t.startswith("policy/") for t in topics_never)

    daemon.outbox.enqueued.clear()
    daemon.decision_cfg.publish = "event"
    daemon._handle_step_result(
        _mk_step(
            event=None,
            decision=decision,
            reward=1.0,
            aoi_ms=10.0,
            mae_est=0.1,
            rate_bps=100.0,
        ),
        label="temp",
        sensor=SensorType.TEMP,
    )
    assert daemon.outbox.enqueued == []

    daemon._handle_step_result(
        _mk_step(
            event=event,
            decision=decision,
            reward=1.0,
            aoi_ms=10.0,
            mae_est=0.1,
            rate_bps=100.0,
        ),
        label="temp",
        sensor=SensorType.TEMP,
    )
    topics_event = [t for (t, *_rest) in daemon.outbox.enqueued]
    assert any(t.startswith("edge/dev1/temp/") for t in topics_event)
    assert any(t.startswith("policy/") for t in topics_event)

    daemon.outbox.enqueued.clear()
    daemon.decision_cfg.publish = "always"
    daemon._handle_step_result(
        _mk_step(
            event=None,
            decision=decision,
            reward=1.0,
            aoi_ms=10.0,
            mae_est=0.1,
            rate_bps=100.0,
        ),
        label="temp",
        sensor=SensorType.TEMP,
    )
    topics_always = [t for (t, *_rest) in daemon.outbox.enqueued]
    assert topics_always == [decision.mqtt_topic()]


def test_emit_marker_enqueues_contract_payload(monkeypatch, tmp_path) -> None:
    daemon = _make_daemon(monkeypatch, tmp_path)
    seen_payload_sizes = []

    def _record_payload(size: int, ts_ns: int) -> None:
        seen_payload_sizes.append((size, ts_ns))

    daemon._status.record_payload = _record_payload  # type: ignore[method-assign]
    daemon._emit_marker()

    assert len(daemon.outbox.enqueued) == 1
    topic, payload, qos, retain, created_ns = daemon.outbox.enqueued[0]
    assert topic == "marker/dev1"
    assert qos == 1
    assert retain is False
    assert isinstance(created_ns, int)

    body = json.loads(payload.decode("utf-8"))
    assert body["device_id"] == "dev1"
    assert body["type"] == "marker"
    assert body["note"] == "button_press"
    assert isinstance(body["ts"], int)

    assert len(seen_payload_sizes) == 1
    assert seen_payload_sizes[0][0] == len(payload)
