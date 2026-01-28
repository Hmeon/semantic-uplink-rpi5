"""Single-Pi supervisor for broker, collector, and edge services.

Spawns mosquitto (optional), collector, and edge processes with shared run
directories and log files. The stack is intended for lab runs and will stop
the whole pipeline if any child exits unexpectedly.
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from common.config import load_device_config
from common.logging_setup import add_logging_cli_args, setup_logging_from_args

logger = logging.getLogger(__name__)


def _opt_path(val: str | None) -> str | None:
    if val is None:
        return None
    sv = str(val).strip()
    if not sv:
        return None
    if sv.lower() in {"none", "null"}:
        return None
    return sv


def _is_localhost(host: str) -> bool:
    h = str(host).strip().lower()
    return h in {"localhost", "127.0.0.1", "::1"}


def _can_connect(host: str, port: int, timeout_s: float = 0.25) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=float(timeout_s)):
            return True
    except OSError:
        return False


def _timestamp_id() -> str:
    return time.strftime("%Y-%m-%dT%H-%M-%SZ", time.gmtime())


@dataclass(slots=True)
class StackConfig:
    """Configuration for the single-Pi stack supervisor.

    Args:
        run_dir: Root directory for run artifacts and logs.
        device_config: Device YAML path for edge daemon.
        policy_arms: Policy arms YAML path for adaptive mode.
        broker_host: MQTT broker host.
        broker_port: MQTT broker port.
        base_topic: Base topic prefix for edge event topics.
        mqtt_username: Optional broker username.
        mqtt_password: Optional broker password.
        mqtt_tls: Enable TLS when True.
        mqtt_cafile: Optional CA file for TLS validation.
        mqtt_certfile: Optional client certificate for TLS.
        mqtt_keyfile: Optional client key for TLS.
        broker_mode: Broker mode ("auto", "subprocess", "none").
        mosquitto_bin: Path to mosquitto executable.
        mosquitto_listen_host: Listen address for mosquitto.
        mosquitto_verbose: Enable verbose mosquitto logging.
        collector_flush_interval_s: Collector flush interval in seconds.
        collector_client_id: Collector MQTT client id.
        edge_client_id: Edge MQTT client id.
        edge_keepalive: MQTT keepalive for edge.
        buttons_enable: Enable GPIO buttons for edge.
        tc_enable: Enable link shaping integration.
        tc_iface: Interface for tc shaping.
        tc_both: Apply tc shaping to ingress when True.
        tc_profiles_config: YAML path for tc profiles.

    Returns:
        None.

    Raises:
        None.

    Side Effects:
        - None.

    Contract:
        - Extra fields are not allowed; this is a dataclass container.

    Failure Modes:
        - Invalid values surface when building child command lines.
    """
    run_dir: str
    device_config: str
    policy_arms: str

    broker_host: str = "localhost"
    broker_port: int = 1883
    base_topic: str = "edge"
    mqtt_username: str | None = None
    mqtt_password: str | None = None
    mqtt_tls: bool = False
    mqtt_cafile: str | None = None
    mqtt_certfile: str | None = None
    mqtt_keyfile: str | None = None
    broker_mode: str = "auto"  # auto | subprocess | none
    mosquitto_bin: str = "mosquitto"
    mosquitto_listen_host: str = "127.0.0.1"
    mosquitto_verbose: bool = False

    collector_flush_interval_s: int = 10
    collector_client_id: str = "collector"

    edge_client_id: str = "edge-pub"
    edge_keepalive: int = 30
    buttons_enable: bool = True

    tc_enable: bool = True
    tc_iface: str = "lo"
    tc_both: bool = False
    tc_profiles_config: str = "configs/link_profiles.yaml"


def _write_mosquitto_conf(cfg: StackConfig, *, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    listener = f"listener {int(cfg.broker_port)} {cfg.mosquitto_listen_host}"
    # persistence는 Outbox가 담당하므로 broker는 디스크 쓰기를 최소화(단일 Pi I/O 보호).
    password_file: Path | None = None
    if cfg.mqtt_username and cfg.mqtt_password:
        mosq_passwd = shutil.which("mosquitto_passwd")
        if mosq_passwd is None:
            logger.warning("mosquitto_passwd not found; leaving broker allow_anonymous=true")
        else:
            password_file = out_path.parent / "mosquitto.passwd"
            try:
                subprocess.check_call(
                    [
                        mosq_passwd,
                        "-b",
                        "-c",
                        str(password_file),
                        str(cfg.mqtt_username),
                        str(cfg.mqtt_password),
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                try:
                    os.chmod(password_file, 0o600)
                except Exception:
                    pass
            except Exception as e:
                logger.warning("failed to generate mosquitto password file: %s", e)
                password_file = None

    lines: list[str] = [
        "# Auto-generated by stack.pi_stack",
        listener,
    ]
    if password_file is not None:
        lines.append("allow_anonymous false")
        lines.append(f"password_file {password_file}")
    else:
        lines.append("allow_anonymous true")
    lines += [
        "persistence false",
        "connection_messages false",
        "log_type error",
        "log_type warning",
        "log_type notice",
        "",
    ]
    text = "\n".join(lines)
    out_path.write_text(text, encoding="utf-8")


def _build_collector_cmd(cfg: StackConfig) -> list[str]:
    cmd = [
        sys.executable,
        "-m",
        "collector.collector",
        "--run-dir",
        cfg.run_dir,
        "--broker",
        cfg.broker_host,
        "--port",
        str(int(cfg.broker_port)),
        "--base-topic",
        str(cfg.base_topic),
        "--flush-interval-s",
        str(int(cfg.collector_flush_interval_s)),
        "--client-id",
        str(cfg.collector_client_id),
    ]
    if cfg.mqtt_username:
        cmd += ["--username", str(cfg.mqtt_username)]
    if cfg.mqtt_password is not None:
        cmd += ["--password", str(cfg.mqtt_password)]
    if bool(cfg.mqtt_tls):
        cmd.append("--tls")
        if cfg.mqtt_cafile:
            cmd += ["--cafile", str(cfg.mqtt_cafile)]
        if cfg.mqtt_certfile:
            cmd += ["--certfile", str(cfg.mqtt_certfile)]
        if cfg.mqtt_keyfile:
            cmd += ["--keyfile", str(cfg.mqtt_keyfile)]
    return cmd


def _build_edge_cmd(cfg: StackConfig) -> list[str]:
    cmd: list[str] = [
        sys.executable,
        "-m",
        "edge.edge_daemon",
        "--device-config",
        cfg.device_config,
        "--run-dir",
        cfg.run_dir,
        "--broker",
        cfg.broker_host,
        "--port",
        str(int(cfg.broker_port)),
        "--base-topic",
        str(cfg.base_topic),
        "--client-id",
        str(cfg.edge_client_id),
        "--keepalive",
        str(int(cfg.edge_keepalive)),
        "--arms",
        cfg.policy_arms,
    ]
    if cfg.mqtt_username:
        cmd += ["--username", str(cfg.mqtt_username)]
    if cfg.mqtt_password is not None:
        cmd += ["--password", str(cfg.mqtt_password)]
    if bool(cfg.mqtt_tls):
        cmd.append("--tls")
        if cfg.mqtt_cafile:
            cmd += ["--cafile", str(cfg.mqtt_cafile)]
        if cfg.mqtt_certfile:
            cmd += ["--certfile", str(cfg.mqtt_certfile)]
        if cfg.mqtt_keyfile:
            cmd += ["--keyfile", str(cfg.mqtt_keyfile)]

    if cfg.buttons_enable:
        cmd.append("--buttons-enable")
    else:
        cmd.append("--buttons-disable")

    if cfg.tc_enable:
        cmd.extend(
            [
                "--tc-apply-on-button",
                "--tc-apply-on-start",
                "--tc-iface",
                cfg.tc_iface,
                "--tc-profiles-config",
                cfg.tc_profiles_config,
            ]
        )
        if cfg.tc_both:
            cmd.append("--tc-both")

    return cmd


@dataclass(slots=True)
class _Child:
    name: str
    popen: subprocess.Popen
    log_path: Path


class PiStack:
    """Supervisor for running broker, collector, and edge together.

    Args:
        cfg: StackConfig with run paths and process settings.

    Returns:
        None.

    Raises:
        None.

    Side Effects:
        - Spawns subprocesses and writes log files.

    Contract:
        - Stops all children when any child exits unexpectedly.

    Failure Modes:
        - Child exit causes non-zero return from run().
    """
    def __init__(self, cfg: StackConfig):
        self.cfg = cfg
        self._stop = False
        self._children: list[_Child] = []

    def run(self) -> int:
        """Run the stack until stopped or a child exits.

        Args:
            None.

        Returns:
            Process exit code (0 on clean shutdown, 1 on child failure).

        Raises:
            ValueError: If broker_mode is invalid.

        Side Effects:
            - Creates run directories and log files.
            - Starts broker/collector/edge subprocesses.

        Contract:
            - Ensures child processes are terminated on exit.

        Failure Modes:
            - Returns non-zero when a child exits unexpectedly.
        """
        self._install_signals()

        run_dir = Path(self.cfg.run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        logs_dir = run_dir / "stack_logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "configs").mkdir(parents=True, exist_ok=True)

        manifest = {
            "run_dir": self.cfg.run_dir,
            "device_config": self.cfg.device_config,
            "policy_arms": self.cfg.policy_arms,
            "base_topic": self.cfg.base_topic,
            "broker": {
                "host": self.cfg.broker_host,
                "port": int(self.cfg.broker_port),
                "mode": self.cfg.broker_mode,
            },
            "edge": {
                "buttons": bool(self.cfg.buttons_enable),
                "tc": bool(self.cfg.tc_enable),
                "tc_iface": self.cfg.tc_iface,
            },
            "collector": {"flush_interval_s": int(self.cfg.collector_flush_interval_s)},
        }
        try:
            from common.jsonutil import dumps as _json_dumps

            (run_dir / "stack_manifest.json").write_bytes(_json_dumps(manifest))
        except Exception:
            import json

            (run_dir / "stack_manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        broker_started = False
        if self.cfg.broker_mode not in {"auto", "subprocess", "none"}:
            raise ValueError("broker_mode must be one of: auto, subprocess, none")

        if self.cfg.broker_mode != "none" and _is_localhost(self.cfg.broker_host):
            already_up = _can_connect(self.cfg.broker_host, int(self.cfg.broker_port))
            if not already_up and self.cfg.broker_mode in {"auto", "subprocess"}:
                if bool(self.cfg.mqtt_tls):
                    logger.error(
                        "mqtt_tls_enabled_but_stack_broker_is_plaintext "
                        "mode=%s host=%s port=%s",
                        self.cfg.broker_mode,
                        self.cfg.broker_host,
                        int(self.cfg.broker_port),
                    )
                    return 1
                broker_started = True
                mosq_conf = run_dir / "configs" / "mosquitto.conf"
                _write_mosquitto_conf(self.cfg, out_path=mosq_conf)
                self._start_mosquitto(mosq_conf, logs_dir / "mosquitto.log")

        if broker_started:
            # race 완화: 포트가 열릴 때까지 짧게 대기
            deadline = time.time() + 3.0
            while time.time() < deadline:
                if _can_connect(self.cfg.broker_host, int(self.cfg.broker_port)):
                    break
                time.sleep(0.05)

        self._start_child("collector", _build_collector_cmd(self.cfg), logs_dir / "collector.log")
        self._start_child("edge", _build_edge_cmd(self.cfg), logs_dir / "edge.log")

        logger.info(
            "stack_running run_dir=%s broker=%s:%s",
            self.cfg.run_dir,
            self.cfg.broker_host,
            int(self.cfg.broker_port),
        )

        try:
            while not self._stop:
                dead = [
                    (c.name, c.popen.returncode)
                    for c in self._children
                    if c.popen.poll() is not None
                ]
                if dead:
                    for name, rc in dead:
                        logger.error("child_exited name=%s rc=%s", name, rc)
                    return 1
                time.sleep(0.25)
        finally:
            self.stop()
        return 0

    def stop(self) -> None:
        """Stop the stack and terminate child processes.

        Args:
            None.

        Returns:
            None.

        Raises:
            None.

        Side Effects:
            - Terminates all child subprocesses.

        Contract:
            - Idempotent; repeated calls are no-ops after first stop.

        Failure Modes:
            - Termination errors are suppressed.
        """
        if self._stop:
            return
        self._stop = True
        self._terminate_all()

    def _install_signals(self) -> None:
        def _h(signum, frame):
            logger.info("stack_signal=%s stopping", signum)
            self.stop()

        try:
            signal.signal(signal.SIGINT, _h)
            signal.signal(signal.SIGTERM, _h)
        except Exception:
            return

    def _start_mosquitto(self, conf: Path, log_path: Path) -> None:
        cmd = [self.cfg.mosquitto_bin, "-c", str(conf)]
        if self.cfg.mosquitto_verbose:
            cmd.append("-v")
        self._start_child("mosquitto", cmd, log_path)

    def _start_child(self, name: str, cmd: list[str], log_path: Path) -> None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        f = open(log_path, "a", encoding="utf-8", buffering=1)
        try:
            popen = subprocess.Popen(
                cmd,
                cwd=os.getcwd(),
                stdout=f,
                stderr=subprocess.STDOUT,
                text=True,
            )
        except Exception:
            f.close()
            raise
        self._children.append(_Child(name=name, popen=popen, log_path=log_path))

    def _terminate_all(self) -> None:
        # reverse order: edge/collector 먼저 종료 → broker 종료
        children = list(reversed(self._children))
        for c in children:
            try:
                c.popen.terminate()
            except Exception:
                continue
        deadline = time.time() + 3.0
        for c in children:
            remaining = max(0.0, deadline - time.time())
            try:
                c.popen.wait(timeout=remaining)
            except Exception:
                pass
        for c in children:
            if c.popen.poll() is None:
                try:
                    c.popen.kill()
                except Exception:
                    pass


def parse_args(argv: list[str] | None = None) -> StackConfig:
    """Parse CLI arguments for the stack supervisor.

    Args:
        argv: Optional argument list for testing; defaults to sys.argv.

    Returns:
        Parsed StackConfig instance.

    Raises:
        SystemExit: If CLI arguments are invalid.

    Side Effects:
        - Configures logging based on CLI flags.

    Contract:
        - Supplies defaults for missing paths.

    Failure Modes:
        - Argument parsing errors exit the process.
    """
    ap = argparse.ArgumentParser(description="Single-Pi stack: broker(mosquitto)+collector+edge")
    add_logging_cli_args(ap)
    ap.add_argument("--run-dir", default="artifacts/live", help="shared run dir for edge+collector")
    ap.add_argument("--device-config", default="configs/device.yaml", help="edge device YAML path")
    ap.add_argument(
        "--policy-arms",
        default="configs/policy.yaml",
        help="edge arms YAML path (adaptive mode)",
    )

    ap.add_argument("--broker-host", default="localhost")
    ap.add_argument("--broker-port", type=int, default=1883)
    ap.add_argument(
        "--base-topic",
        default=None,
        help="base topic prefix for edge events (default: from device config, else edge)",
    )
    ap.add_argument("--mqtt-username", default=None, help="MQTT username (optional)")
    ap.add_argument("--mqtt-password", default=None, help="MQTT password (optional)")
    ap.add_argument(
        "--mqtt-tls",
        dest="mqtt_tls",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="enable MQTT TLS (default: from device config, else false)",
    )
    ap.add_argument("--mqtt-cafile", default=None, help="CA file for MQTT TLS (optional)")
    ap.add_argument("--mqtt-certfile", default=None, help="client cert for MQTT TLS (optional)")
    ap.add_argument("--mqtt-keyfile", default=None, help="client key for MQTT TLS (optional)")
    ap.add_argument("--broker-mode", choices=["auto", "subprocess", "none"], default="auto")
    ap.add_argument("--mosquitto-bin", default="mosquitto")
    ap.add_argument("--mosquitto-listen-host", default="127.0.0.1")
    ap.add_argument("--mosquitto-verbose", action="store_true")

    ap.add_argument("--collector-flush-interval-s", type=int, default=10)
    ap.add_argument("--collector-client-id", default="collector")

    ap.add_argument("--edge-client-id", default="edge-pub")
    ap.add_argument("--edge-keepalive", type=int, default=30)

    btn = ap.add_mutually_exclusive_group()
    btn.add_argument("--buttons-enable", dest="buttons_enable", action="store_true")
    btn.add_argument("--buttons-disable", dest="buttons_enable", action="store_false")
    ap.set_defaults(buttons_enable=True)

    tc = ap.add_mutually_exclusive_group()
    tc.add_argument("--tc-enable", dest="tc_enable", action="store_true")
    tc.add_argument("--tc-disable", dest="tc_enable", action="store_false")
    ap.set_defaults(tc_enable=True)
    ap.add_argument("--tc-iface", default="lo")
    ap.add_argument("--tc-both", action="store_true")
    ap.add_argument("--tc-profiles-config", default="configs/link_profiles.yaml")

    args = ap.parse_args(argv)
    setup_logging_from_args(args)

    run_dir = _opt_path(args.run_dir) or f"artifacts/{_timestamp_id()}_live"
    device_config = _opt_path(args.device_config) or "configs/device.yaml"
    policy_arms = _opt_path(args.policy_arms) or "configs/policy.yaml"

    device_cfg = None
    try:
        device_cfg = load_device_config(device_config)
    except Exception:
        device_cfg = None

    base_topic = _opt_path(args.base_topic)
    if base_topic is None:
        base_topic = str(device_cfg.mqtt.base_topic) if device_cfg is not None else "edge"

    mqtt_username = _opt_path(args.mqtt_username)
    if mqtt_username is None and device_cfg is not None:
        mqtt_username = device_cfg.mqtt.username
    mqtt_password = _opt_path(args.mqtt_password)
    if mqtt_password is None and device_cfg is not None:
        mqtt_password = device_cfg.mqtt.password
    mqtt_tls = (
        bool(args.mqtt_tls)
        if args.mqtt_tls is not None
        else (bool(device_cfg.mqtt.tls) if device_cfg is not None else False)
    )
    mqtt_cafile = _opt_path(args.mqtt_cafile)
    if mqtt_cafile is None and device_cfg is not None:
        mqtt_cafile = device_cfg.mqtt.cafile
    mqtt_certfile = _opt_path(args.mqtt_certfile)
    if mqtt_certfile is None and device_cfg is not None:
        mqtt_certfile = device_cfg.mqtt.certfile
    mqtt_keyfile = _opt_path(args.mqtt_keyfile)
    if mqtt_keyfile is None and device_cfg is not None:
        mqtt_keyfile = device_cfg.mqtt.keyfile

    return StackConfig(
        run_dir=run_dir,
        device_config=device_config,
        policy_arms=policy_arms,
        broker_host=str(args.broker_host),
        broker_port=int(args.broker_port),
        base_topic=base_topic,
        mqtt_username=mqtt_username,
        mqtt_password=mqtt_password,
        mqtt_tls=bool(mqtt_tls),
        mqtt_cafile=mqtt_cafile,
        mqtt_certfile=mqtt_certfile,
        mqtt_keyfile=mqtt_keyfile,
        broker_mode=str(args.broker_mode),
        mosquitto_bin=str(args.mosquitto_bin),
        mosquitto_listen_host=str(args.mosquitto_listen_host),
        mosquitto_verbose=bool(args.mosquitto_verbose),
        collector_flush_interval_s=int(args.collector_flush_interval_s),
        collector_client_id=str(args.collector_client_id),
        edge_client_id=str(args.edge_client_id),
        edge_keepalive=int(args.edge_keepalive),
        buttons_enable=bool(args.buttons_enable),
        tc_enable=bool(args.tc_enable),
        tc_iface=str(args.tc_iface),
        tc_both=bool(args.tc_both),
        tc_profiles_config=str(args.tc_profiles_config),
    )


def main(argv: list[str] | None = None) -> None:
    """CLI entry point for the stack supervisor.

    Args:
        argv: Optional argument list for testing; defaults to sys.argv.

    Returns:
        None.

    Raises:
        SystemExit: When the stack exits with a non-zero code.

    Side Effects:
        - Starts broker/collector/edge subprocesses.

    Contract:
        - Exits with the same code as PiStack.run().

    Failure Modes:
        - Propagates SystemExit on fatal errors.
    """
    cfg = parse_args(argv)
    rc = PiStack(cfg).run()
    raise SystemExit(rc)


if __name__ == "__main__":
    main()
