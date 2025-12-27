from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from queue import Queue

import pandas as pd
import pytest

from common.schema import EventMsg, LinkProfile, PolicyMode, SensorType


def _docker_bind_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/")


def _docker_available() -> bool:
    return shutil.which("docker") is not None


def _docker(*args: str, timeout_s: float = 30.0) -> str:
    out = subprocess.check_output(
        ["docker", *args],
        text=True,
        stderr=subprocess.STDOUT,
        timeout=timeout_s,
    )
    return out.strip()


def _extract_container_id(output: str) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    for line in reversed(lines):
        if len(line) >= 12 and all(c in "0123456789abcdef" for c in line.lower()):
            return line
    raise RuntimeError(f"failed to extract container id from docker output: {output!r}")


def _wait_for_tcp(host: str, port: int, *, timeout_s: float = 10.0) -> None:
    deadline = time.time() + timeout_s
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return
        except OSError as exc:
            last_err = exc
            time.sleep(0.2)
    raise RuntimeError(f"broker not reachable at {host}:{port}. last_error={last_err}")


def _get_container_port(container_id: str, *, timeout_s: float = 10.0) -> int:
    deadline = time.time() + timeout_s
    last: str | None = None
    while time.time() < deadline:
        try:
            out = _docker("port", container_id, "1883/tcp", timeout_s=5.0)
            if not out:
                time.sleep(0.2)
                continue
            mapping = out.splitlines()[0].strip()
            host_port = int(mapping.rsplit(":", 1)[-1])
            return host_port
        except Exception as exc:
            last = str(exc)
            time.sleep(0.2)
    raise RuntimeError(f"failed to resolve broker port from container {container_id}: {last}")


@contextmanager
def _mosquitto_broker(tmp_path: Path) -> Iterator[tuple[str, int]]:
    if not _docker_available():
        pytest.skip("docker is required for the MQTT E2E test")

    conf = tmp_path / "mosquitto.conf"
    conf.write_text(
        "\n".join(
            [
                "listener 1883 0.0.0.0",
                "allow_anonymous true",
                "persistence false",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    try:
        container_out = _docker(
            "run",
            "-d",
            "--rm",
            "-p",
            "0:1883",
            "-v",
            f"{_docker_bind_path(conf)}:/mosquitto/config/mosquitto.conf:ro",
            "eclipse-mosquitto:2.0.18",
            timeout_s=60.0,
        )
        container_id = _extract_container_id(container_out)
    except subprocess.CalledProcessError as exc:
        pytest.skip(f"docker unavailable/unhealthy: {exc.output}")

    try:
        host = "127.0.0.1"
        port = _get_container_port(container_id)
        _wait_for_tcp(host, port, timeout_s=15.0)
        yield (host, port)
    finally:
        subprocess.run(
            ["docker", "stop", container_id],
            check=False,
            capture_output=True,
            text=True,
        )


def _spawn_reader(proc: subprocess.Popen[str], q: Queue[str]) -> threading.Thread:
    def _reader() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            q.put(line)

    t = threading.Thread(target=_reader, daemon=True)
    t.start()
    return t


def _wait_for_substring(lines: Queue[str], needle: str, *, timeout_s: float = 10.0) -> list[str]:
    deadline = time.time() + timeout_s
    seen: list[str] = []
    while time.time() < deadline:
        try:
            line = lines.get(timeout=0.2)
            seen.append(line)
            if needle in line:
                return seen
        except Exception:
            pass
    raise AssertionError(f"timeout waiting for {needle!r}. output:\n{''.join(seen)}")


def _publish_events(host: str, port: int, events: list[EventMsg]) -> None:
    import paho.mqtt.client as mqtt

    client = mqtt.Client(client_id="pytest-pub", protocol=mqtt.MQTTv311)
    client.connect(host, port, keepalive=30)
    client.loop_start()
    try:
        for ev in events:
            info = client.publish(ev.mqtt_topic(), ev.to_json_bytes(), qos=1, retain=False)
            info.wait_for_publish(timeout=5.0)
            time.sleep(0.02)
    finally:
        client.loop_stop()
        client.disconnect()


def test_end_to_end_mqtt_collector_analyze(tmp_path: Path) -> None:
    run_dir = tmp_path / "run1"
    out_dir = tmp_path / "analysis"

    with _mosquitto_broker(tmp_path) as (host, port):
        env = {**os.environ, "PYTHONUNBUFFERED": "1"}
        proc = subprocess.Popen(
            [
                sys.executable,
                "-u",
                "-m",
                "collector.collector",
                "--run-dir",
                str(run_dir),
                "--broker",
                host,
                "--port",
                str(port),
                "--flush-interval-s",
                "1",
                "--client-id",
                "collector-e2e",
                "--max-runtime-s",
                "3",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )
        lines: Queue[str] = Queue()
        _spawn_reader(proc, lines)
        _wait_for_substring(lines, "subscribed topics", timeout_s=15.0)

        device_id = "dev1"
        profile = LinkProfile.SLOW_10KBPS
        sensor = SensorType.TEMP
        kbits = 8
        base_ts = time.time_ns()

        def _ev(seq: int, policy: PolicyMode, tau: float) -> EventMsg:
            val = float(seq) * 0.1
            pred = val - 0.05
            res = val - pred
            return EventMsg(
                ts=int(base_ts + (seq * 50_000_000)),
                seq=int(seq),
                device_id=device_id,
                sensor=sensor,
                val=val,
                pred=pred,
                res=res,
                tau=float(tau),
                kbits=int(kbits),
                profile=profile,
                policy=policy,
            )

        events_unique: list[EventMsg] = []
        # periodic: seq 1..5
        for seq in range(1, 6):
            events_unique.append(_ev(seq, PolicyMode.PERIODIC, tau=-1e-9))
        # fixed_tau: seq 6..10
        for seq in range(6, 11):
            events_unique.append(_ev(seq, PolicyMode.FIXED_TAU, tau=0.2))

        _publish_events(host, port, events_unique)

        # Ensure duplicates are sent after at least one flush (tests cross-flush de-dup).
        _wait_for_substring(lines, "flush events+=", timeout_s=15.0)
        dup_events = [
            _ev(2, PolicyMode.PERIODIC, tau=-1e-9),
            _ev(7, PolicyMode.FIXED_TAU, tau=0.2),
        ]
        _publish_events(host, port, dup_events)

        try:
            proc.wait(timeout=20.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            raise
        assert proc.returncode == 0

    logs_dir = run_dir / "logs"
    meta_path = run_dir / "logs" / "collector_meta.json"
    events_paths = sorted(logs_dir.glob("events_*.parquet"))
    if not events_paths:
        legacy = logs_dir / "events.parquet"
        if legacy.exists():
            events_paths = [legacy]
    assert events_paths
    assert meta_path.exists()

    df = pd.concat([pd.read_parquet(p) for p in events_paths], ignore_index=True)
    assert len(df) == 10  # unique seq only

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert int(meta["events_unique"]) == 10
    assert int(meta["dup_messages_dropped"]) >= 2

    env = {**os.environ, "MPLBACKEND": "Agg"}
    subprocess.run(
        [
            sys.executable,
            "-m",
            "collector.analyze",
            "--input",
            str(run_dir),
            "--out",
            str(out_dir),
            "--no-paper-plots",
        ],
        check=True,
        env=env,
        capture_output=True,
        text=True,
        timeout=60.0,
    )

    summary_path = out_dir / "metrics_summary.csv"
    cmp_path = out_dir / "metrics_vs_periodic.csv"
    figs_dir = out_dir / "figures"
    assert summary_path.exists()
    assert cmp_path.exists()
    assert figs_dir.exists()
    assert list(figs_dir.glob("*.png"))
