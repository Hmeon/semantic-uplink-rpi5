# experiments/run_scenarios.py
# Python 3.10+
# 목적: 링크 프로파일 × 모드(periodic/fixed_tau/[adaptive]) 자동 실행 스크립트.
# 단계: tc 적용 → edge_daemon(+collector 옵션) → warmup/run/cooldown → 종료/정리 → 결과 폴더 고정.
# - 재현성: 폴더/파일명 규칙, 매니페스트(JSON), stdout/stderr 리디렉션, 파라미터 스냅샷
# - 안정성: SIGINT/SIGTERM 처리, tc 원복 보장, edge/collector 프로세스 정리
# - 의존: 표준 라이브러리 + 프로젝트 내부 모듈(link.shaper.tc_profiles.apply_profile/clear)

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import signal
import subprocess
import sys
import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

from common.config import load_device_config, load_link_profiles_config, load_policy_config_dict
from common.schema import LinkProfile, PolicyMode

# link.shaper.tc_profiles의 함수 직접 호출 (cellular_var 토글 스레드가 CLI 블로킹을 유발하므로)
try:
    from link.shaper.tc_profiles import apply_profile as tc_apply
    from link.shaper.tc_profiles import clear as tc_clear
    from link.shaper.tc_profiles import get_profiles as tc_get
    from link.shaper.tc_profiles import load_profiles_config as tc_load_profiles
except Exception as e:  # pragma: no cover
    print(f"[exp] FATAL: cannot import tc_profiles: {e}", file=sys.stderr)
    sys.exit(2)


@dataclass(slots=True)
class ExperimentPlan:
    device_id: str = "rpi5-01"
    device_config: str | None = "configs/device.yaml"
    iface: str = "eth0"
    both: bool = False            # ingress(ifb0) 포함 여부
    run_root: Path = Path("artifacts/experiments")
    link_profiles_config: str | None = "configs/link_profiles.yaml"

    # 시간(초)
    warmup_s: int = 10
    run_s: int = 120
    cooldown_s: int = 5

    # 브로커
    broker: str = "localhost"
    port: int = 1883
    seed: int = 0

    # 센서 사용
    use_mic: bool = True
    use_temp: bool = True

    # MIC 파라미터(주요값만 노출)
    mic_sr: int = 16000
    mic_frame_ms: int = 100
    mic_alpha: float = 0.2
    mic_tau_fixed: float = 3.0
    mic_kbits: int = 6
    mic_heartbeat_s: float = 10.0

    # TEMP 파라미터
    temp_hz: float = 1.0
    temp_alpha: float = 0.5
    temp_tau_fixed: float = 0.2
    temp_kbits: int = 8
    temp_heartbeat_s: float = 10.0

    arms_path: str = "configs/policy.yaml"

    # 모드/프로파일
    modes: tuple[str, ...] = ("periodic", "fixed_tau", "adaptive")
    profiles: tuple[str, ...] = ("slow_10kbps", "delay_loss", "cellular_var")

    # 실행 옵션
    claim_batch: int = 10
    max_inflight: int = 10
    with_collector: bool = False    # collector 프로세스 병행 (수집기 구현 수준에 따라 False 권장)
    tc_var_period_s: int | None = None  # cellular_var 토글 주기(초); None=프로파일 기본
    repeats: int = 1                  # 시나리오 반복 횟수(리플리케이트)
    collector_flush_interval_s: int = 10


@dataclass(slots=True)
class Scenario:
    profile: LinkProfile
    mode: PolicyMode
    name: str
    out_dir: Path


class ScenarioRunner:
    def __init__(self, plan: ExperimentPlan):
        self.plan = plan
        self._stop = False
        self._active_tc = False
        self._procs: list[subprocess.Popen] = []
        self._tc_profiles_override = self._load_tc_profiles_override()
        # 신호 처리
        signal.signal(signal.SIGINT, self._on_signal)
        signal.signal(signal.SIGTERM, self._on_signal)

    # --------- 공개 메서드 ---------

    def run_all(self, scenarios: Iterable[Scenario]) -> None:
        for sc in scenarios:
            if self._stop:
                break
            self._run_one(sc)

    # --------- 내부: 시나리오 실행 ---------

    def _run_one(self, sc: Scenario) -> None:
        p = self.plan
        sc.out_dir.mkdir(parents=True, exist_ok=True)
        log_edge = (sc.out_dir / "edge_daemon.log").open("w", buffering=1)
        log_col = (sc.out_dir / "collector.log").open("w", buffering=1)
        meta = {
            "scenario": {"name": sc.name, "profile": sc.profile.value, "mode": sc.mode.value},
            "timestamps": {"created_utc": _utc_ts()},
            "plan": _asdict_plan(p),
            "env": _env_snapshot(),
        }
        self._write_manifest(sc, meta)

        print(f"[exp] === RUN {sc.name} ===")
        try:
            # 1) tc 적용
            self._apply_profile(sc.profile)

            # 2) collector (옵션) - 먼저 띄워서 초기 이벤트 누락을 줄임
            col_proc = None
            if p.with_collector:
                col_cmd = self._collector_cmd(sc)
                if col_cmd:
                    col_proc = self._popen(col_cmd, stdout=log_col, stderr=subprocess.STDOUT)
                    print(f"[exp] collector pid={col_proc.pid}")

            # 3) edge_daemon 실행
            edge_cmd = self._edge_cmd(sc)
            edge_proc = self._popen(edge_cmd, stdout=log_edge, stderr=subprocess.STDOUT)
            print(f"[exp] edge_daemon pid={edge_proc.pid}")

            # 4) Warmup → Run → Cooldown
            self._phase_sleep("warmup", p.warmup_s)
            self._phase_sleep("run", p.run_s)
            self._phase_sleep("cooldown", p.cooldown_s)

        finally:
            # 5) 종료/정리
            self._terminate_all()
            self._clear_profile()
            log_edge.close()
            log_col.close()
            print(f"[exp] --- DONE {sc.name} ---\n")

    # --------- 명령 생성 ---------

    def _edge_cmd(self, sc: Scenario) -> list[str]:
        """edge.edge_daemon CLI 인자 구성 (periodic은 τ<0으로 에뮬레이션)."""
        p = self.plan
        run_dir = sc.out_dir
        base = [
            sys.executable, "-m", "edge.edge_daemon",
            "--device-id", p.device_id,
            "--profile", sc.profile.value,
            "--mode", sc.mode.value,
            "--broker", p.broker,
            "--port", str(p.port),
            "--client-id", f"edge-{p.device_id}",
            "--run-dir", str(run_dir),
            "--seed", str(p.seed),
            "--arms", p.arms_path,
            # 실험 러너는 headless가 기본: device.yaml에서 UI가 켜져 있어도 비활성화한다.
            "--ui-disable",
            "--buttons-disable",
        ]

        if p.device_config:
            base += ["--device-config", p.device_config]

        if p.use_mic:
            base += ["--mic-enable",
                     "--mic-sr", str(p.mic_sr),
                     "--mic-frame-ms", str(p.mic_frame_ms),
                     "--mic-alpha", str(p.mic_alpha),
                     "--mic-kbits", str(p.mic_kbits),
                     "--mic-heartbeat", str(p.mic_heartbeat_s)]
            base += ["--mic-tau", str(p.mic_tau_fixed)]
        else:
            base += ["--mic-disable"]

        if p.use_temp:
            base += ["--temp-enable",
                     "--temp-hz", str(p.temp_hz),
                     "--temp-alpha", str(p.temp_alpha),
                     "--temp-kbits", str(p.temp_kbits),
                     "--temp-heartbeat", str(p.temp_heartbeat_s)]
            base += ["--temp-tau", str(p.temp_tau_fixed)]
        else:
            base += ["--temp-disable"]

        return base

    def _collector_cmd(self, sc: Scenario) -> list[str] | None:
        """
        collector가 모듈로 제공되는 경우에만 실행.
        구현/CLI가 상이할 수 있으므로 최소 인자만 제공(브로커/출력경로).
        """
        try:
            __import__("collector.collector")
        except Exception:
            print("[exp] WARN: collector module not available; skipping collector.")
            return None
        return [
            sys.executable, "-m", "collector.collector",
            "--run-dir", str(sc.out_dir),
            "--broker", self.plan.broker,
            "--port", str(self.plan.port),
            "--flush-interval-s", str(self.plan.collector_flush_interval_s),
            "--client-id", f"collector-{self.plan.device_id}",
        ]

    # --------- tc 프로파일 적용/해제 ---------

    def _apply_profile(self, profile: LinkProfile) -> None:
        geteuid = getattr(os, "geteuid", None)
        if geteuid is None:
            print("[exp] WARN: tc shaping is not supported on this OS; skipping.", file=sys.stderr)
            self._active_tc = False
            return
        if geteuid is not None and geteuid() != 0:
            print("[exp] WARN: not running as root; skipping tc shaping.", file=sys.stderr)
            self._active_tc = False
            return
        try:
            varp = self.plan.tc_var_period_s
            tc_apply(
                self.plan.iface,
                profile.value,
                both=self.plan.both,
                var_period_s=varp,
                profiles=self._tc_profiles_override,
            )
            self._active_tc = True
            print(
                f"[exp] tc applied: iface={self.plan.iface} both={self.plan.both} "
                f"profile={profile.value}"
            )
        except PermissionError as e:
            print(f"[exp] WARN: tc apply failed (not root?): {e}", file=sys.stderr)
            self._active_tc = False

    def _load_tc_profiles_override(self):
        """
        link_profiles_config가 지정된 경우, tc 프로파일을 YAML에서 로딩한다.
        - 실패 시 None으로 폴백(내장 PROFILES 사용).
        """
        path = self.plan.link_profiles_config
        if not path:
            return None
        p = Path(path)
        if not p.exists():
            return None
        try:
            return tc_load_profiles(p)
        except Exception as e:
            print(f"[exp] WARN: failed to load link profiles: {p}: {e}", file=sys.stderr)
            return None

    def _clear_profile(self) -> None:
        if self._active_tc:
            try:
                tc_clear(self.plan.iface, both=self.plan.both)
            except Exception as e:
                print(f"[exp] WARN: tc clear error: {e}", file=sys.stderr)
            self._active_tc = False
            print("[exp] tc cleared")

    # --------- 유틸 ---------

    def _popen(self, cmd: list[str], **kw) -> subprocess.Popen:
        env = dict(os.environ)
        user_env = kw.pop("env", None)
        if isinstance(user_env, dict):
            env.update(user_env)
        env.setdefault("PYTHONHASHSEED", str(self.plan.seed))
        env.setdefault("SEMUP_SEED", str(self.plan.seed))
        kw["env"] = env
        proc = subprocess.Popen(cmd, **kw)
        self._procs.append(proc)
        return proc

    def _terminate_all(self) -> None:
        # 먼저 SIGINT → 대기 → 잔여 SIGKILL
        for p in list(self._procs):
            if p.poll() is None:
                try:
                    p.send_signal(signal.SIGINT)
                except Exception:
                    pass
        _deadline = time.time() + 3.0
        while time.time() < _deadline and any(p.poll() is None for p in self._procs):
            time.sleep(0.1)
        for p in list(self._procs):
            if p.poll() is None:
                try:
                    p.kill()
                except Exception:
                    pass
        self._procs.clear()

    def _phase_sleep(self, name: str, seconds: int) -> None:
        if seconds <= 0:
            return
        print(f"[exp] phase={name} {seconds}s")
        t0 = time.time()
        while not self._stop and (time.time() - t0) < seconds:
            time.sleep(0.25)

    def _write_manifest(self, sc: Scenario, meta: dict) -> None:
        (sc.out_dir / "manifest.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))

    def _on_signal(self, signum, frame):
        print(f"[exp] SIGNAL={signum} -> stopping current scenario and cleaning up...")
        self._stop = True
        self._terminate_all()
        self._clear_profile()


# ---------- 헬퍼 ----------

def build_scenarios(plan: ExperimentPlan) -> list[Scenario]:
    root = _run_root(plan)
    scenarios: list[Scenario] = []
    for prof_s in plan.profiles:
        lp = LinkProfile(prof_s)
        for mode_s in plan.modes:
            pm = PolicyMode(mode_s)
            for rep in range(max(1, int(plan.repeats))):
                name = (
                    f"{lp.value}__{pm.value}"
                    if int(plan.repeats) <= 1
                    else f"{lp.value}__{pm.value}__rep{rep + 1:02d}"
                )
                out_dir = root / name
                scenarios.append(Scenario(profile=lp, mode=pm, name=name, out_dir=out_dir))
    return scenarios


def _run_root(plan: ExperimentPlan) -> Path:
    ts = time.strftime("%Y-%m-%dT%H-%M-%SZ", time.gmtime())
    root = plan.run_root / f"{ts}_{plan.device_id}"
    root.mkdir(parents=True, exist_ok=True)
    # 실행 스냅샷(계획)
    (root / "plan.json").write_text(json.dumps(_asdict_plan(plan), indent=2, ensure_ascii=False))
    # 사용 가능한 tc 프로파일 목록 기록(참고)
    try:
        tc_src = tc_get()
        if plan.link_profiles_config and Path(plan.link_profiles_config).exists():
            try:
                tc_src = tc_load_profiles(plan.link_profiles_config)
            except Exception:
                tc_src = tc_get()
        tc_profiles = {k: asdict(v) for k, v in tc_src.items()}
        (root / "tc_profiles.json").write_text(json.dumps(tc_profiles, indent=2))
    except Exception:
        pass
    _write_run_meta(root, plan)
    return root


def _utc_ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _git_commit() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], timeout=2).decode().strip()
    except Exception:
        return None


def _read_text_snapshot(path: Path) -> dict:
    snap = {"path": str(path), "exists": path.exists()}
    if not path.exists():
        snap["sha256"] = None
        snap["text"] = None
        return snap
    data = path.read_bytes()
    snap["sha256"] = hashlib.sha256(data).hexdigest()
    snap["text"] = path.read_text(encoding="utf-8", errors="replace")
    return snap


def _write_run_meta(root: Path, plan: ExperimentPlan) -> None:
    device_cfg_path = Path(plan.device_config) if plan.device_config else None
    link_cfg_path = Path(plan.link_profiles_config) if plan.link_profiles_config else None
    meta = {
        "created_utc": _utc_ts(),
        "git_commit": _git_commit(),
        "seed": int(plan.seed),
        "python": {"version": sys.version, "executable": sys.executable},
        "platform": {
            "platform": platform.platform(),
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "processor": platform.processor(),
        },
        "configs": {
            "policy_yaml": _read_text_snapshot(Path(plan.arms_path)),
            "device_yaml": (
                _read_text_snapshot(device_cfg_path) if device_cfg_path is not None else None
            ),
            "link_profiles_yaml": (
                _read_text_snapshot(link_cfg_path) if link_cfg_path is not None else None
            ),
        },
    }
    tmp = root / "run_meta.json.tmp"
    out = root / "run_meta.json"
    tmp.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(out)


def _asdict_plan(plan: ExperimentPlan) -> dict:
    d = asdict(plan)
    d["run_root"] = str(plan.run_root)
    return d


def _env_snapshot() -> dict:
    info = {
        "python": sys.version,
        "argv": sys.argv,
        "cwd": str(Path.cwd()),
    }
    # git 커밋(옵션)
    info["git_commit"] = _git_commit()
    info["platform"] = platform.platform()
    return info


# ---------- CLI ----------

def parse_args(argv: list[str] | None = None) -> ExperimentPlan:
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--device-config", default="configs/device.yaml")
    pre.add_argument("--link-profiles-config", default="configs/link_profiles.yaml")
    pre_args, _ = pre.parse_known_args(argv)

    device_config = _opt_path(pre_args.device_config)
    link_profiles_config = _opt_path(pre_args.link_profiles_config)

    device_cfg = None
    if device_config:
        try:
            device_cfg = load_device_config(device_config)
        except Exception as e:
            print(f"[exp] ERROR: invalid device config {device_config}: {e}", file=sys.stderr)
            sys.exit(2)

    if link_profiles_config:
        try:
            load_link_profiles_config(link_profiles_config)
        except Exception as e:
            print(
                f"[exp] ERROR: invalid link profiles config {link_profiles_config}: {e}",
                file=sys.stderr,
            )
            sys.exit(2)

    device_id_default = device_cfg.device_id if device_cfg is not None else "rpi5-01"
    broker_default = device_cfg.mqtt.host if device_cfg is not None else "localhost"
    port_default = int(device_cfg.mqtt.port) if device_cfg is not None else 1883
    use_mic_default = bool(device_cfg.sensors.mic is not None) if device_cfg is not None else True
    use_temp_default = bool(device_cfg.sensors.temp is not None) if device_cfg is not None else True
    mic_sr_default = (
        int(device_cfg.sensors.mic.samplerate)
        if device_cfg is not None and device_cfg.sensors.mic is not None
        else 16000
    )
    mic_frame_ms_default = (
        int(device_cfg.sensors.mic.frame_ms)
        if device_cfg is not None and device_cfg.sensors.mic is not None
        else 100
    )
    temp_hz_default = (
        float(device_cfg.sensors.temp.period_hz)
        if device_cfg is not None and device_cfg.sensors.temp is not None
        else 1.0
    )

    ap = argparse.ArgumentParser(
        description="Run profile×mode matrix experiments (edge_daemon orchestrator)"
    )
    ap.add_argument(
        "--device-config",
        default=device_config,
        help="device YAML (used to fill defaults)",
    )
    ap.add_argument(
        "--link-profiles-config",
        default=link_profiles_config,
        help="tc profile YAML (used to override built-in PROFILES when applying shaping)",
    )
    ap.add_argument("--device-id", default=device_id_default)
    ap.add_argument("--iface", default="eth0")
    ap.add_argument("--both", action="store_true")
    ap.add_argument("--run-root", default="artifacts/experiments")

    ap.add_argument("--warmup-s", type=int, default=10)
    ap.add_argument("--run-s", type=int, default=120)
    ap.add_argument("--cooldown-s", type=int, default=5)

    ap.add_argument("--broker", default=broker_default)
    ap.add_argument("--port", type=int, default=port_default)
    ap.add_argument("--seed", type=int, default=0, help="random seed for reproducibility")

    # 기본은 둘 다 활성(평가/데모 기준). 필요 시 --no-mic / --no-temp 로 비활성.
    ap.add_argument(
        "--mic", dest="use_mic", action=argparse.BooleanOptionalAction, default=use_mic_default
    )
    ap.add_argument(
        "--temp", dest="use_temp", action=argparse.BooleanOptionalAction, default=use_temp_default
    )
    # backward compatible(숨김): 예전 플래그
    ap.add_argument("--use-mic", dest="use_mic", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--use-temp", dest="use_temp", action="store_true", help=argparse.SUPPRESS)

    ap.add_argument("--mic-sr", type=int, default=mic_sr_default)
    ap.add_argument("--mic-frame-ms", type=int, default=mic_frame_ms_default)
    ap.add_argument("--mic-alpha", type=float, default=0.2)
    ap.add_argument("--mic-tau-fixed", type=float, default=3.0)
    ap.add_argument("--mic-kbits", type=int, default=6)
    ap.add_argument("--mic-heartbeat", type=float, default=10.0)

    ap.add_argument("--temp-hz", type=float, default=temp_hz_default)
    ap.add_argument("--temp-alpha", type=float, default=0.5)
    ap.add_argument("--temp-tau-fixed", type=float, default=0.2)
    ap.add_argument("--temp-kbits", type=int, default=8)
    ap.add_argument("--temp-heartbeat", type=float, default=10.0)

    ap.add_argument("--arms-path", default="configs/policy.yaml", help="LinUCB arms config path")
    ap.add_argument("--modes", default="periodic,fixed_tau,adaptive",
                    help="comma-separated: periodic,fixed_tau,adaptive")
    ap.add_argument("--profiles", default="slow_10kbps,delay_loss,cellular_var",
                    help="comma-separated profile names")
    ap.add_argument("--with-collector", action="store_true")
    ap.add_argument(
        "--tc-var-period",
        type=int,
        default=None,
        help="cellular_var toggle period (seconds)",
    )
    ap.add_argument(
        "--repeats",
        type=int,
        default=1,
        help="scenario repeats (replicates) per profile×mode",
    )
    ap.add_argument("--collector-flush-interval-s", type=int, default=10)
    args = ap.parse_args(argv)

    plan = ExperimentPlan(
        device_id=args.device_id,
        device_config=args.device_config,
        iface=args.iface,
        both=bool(args.both),
        run_root=Path(args.run_root),
        link_profiles_config=args.link_profiles_config,

        warmup_s=args.warmup_s,
        run_s=args.run_s,
        cooldown_s=args.cooldown_s,

        broker=args.broker,
        port=args.port,
        seed=int(args.seed),

        use_mic=bool(args.use_mic),
        use_temp=bool(args.use_temp),

        mic_sr=args.mic_sr,
        mic_frame_ms=args.mic_frame_ms,
        mic_alpha=args.mic_alpha,
        mic_tau_fixed=args.mic_tau_fixed,
        mic_kbits=args.mic_kbits,
        mic_heartbeat_s=args.mic_heartbeat,

        temp_hz=args.temp_hz,
        temp_alpha=args.temp_alpha,
        temp_tau_fixed=args.temp_tau_fixed,
        temp_kbits=args.temp_kbits,
        temp_heartbeat_s=args.temp_heartbeat,

        arms_path=args.arms_path,
        modes=tuple(m.strip() for m in args.modes.split(",") if m.strip()),
        profiles=tuple(p.strip() for p in args.profiles.split(",") if p.strip()),
        with_collector=bool(args.with_collector),
        tc_var_period_s=args.tc_var_period if args.tc_var_period is not None else None,
        repeats=max(1, int(args.repeats)),
        collector_flush_interval_s=max(1, int(args.collector_flush_interval_s)),
    )
    # 안전장치: 최소 한 센서 활성
    if not plan.use_mic and not plan.use_temp:
        print("[exp] ERROR: enable at least one sensor (--mic or --temp)", file=sys.stderr)
        sys.exit(2)
    # 모드/프로파일 유효성
    for m in plan.modes:
        _ = PolicyMode(m)
    for p in plan.profiles:
        _ = LinkProfile(p)

    if "adaptive" in plan.modes:
        try:
            load_policy_config_dict(plan.arms_path)
        except Exception as e:
            print(f"[exp] ERROR: invalid arms config {plan.arms_path}: {e}", file=sys.stderr)
            sys.exit(2)

    # baseline 비교(collector.analyze의 기본 baseline=periodic) 관점에서 경고 제공
    if "periodic" not in plan.modes:
        print(
            "[exp] WARN: periodic baseline is not in modes; "
            "analyze baseline comparisons may be NaN.",
            file=sys.stderr,
        )

    return plan


def _opt_path(value: str | None) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if s.lower() in {"none", "null", "nil"}:
        return None
    return s


def main():
    plan = parse_args()
    scenarios = build_scenarios(plan)
    runner = ScenarioRunner(plan)
    runner.run_all(scenarios)


if __name__ == "__main__":
    main()

