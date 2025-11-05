# experiments/run_scenarios.py
# Python 3.10+
# 목적: 링크 프로파일 × 모드(periodic/fixed_tau/[adaptive]) 자동 실행 스크립트.
# 단계: tc 적용 → edge_daemon(+collector 옵션) → warmup/run/cooldown → 종료/정리 → 결과 폴더 고정.
# - 재현성: 폴더/파일명 규칙, 매니페스트(JSON), stdout/stderr 리디렉션, 파라미터 스냅샷
# - 안정성: SIGINT/SIGTERM 처리, tc 원복 보장, edge/collector 프로세스 정리
# - 의존: 표준 라이브러리 + 프로젝트 내부 모듈(link.shaper.tc_profiles.apply_profile/clear)

from __future__ import annotations

import argparse
import json
import os
import shlex
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from common.schema import LinkProfile, PolicyMode

# link.shaper.tc_profiles의 함수 직접 호출 (cellular_var 토글 스레드가 CLI 블로킹을 유발하므로)
try:
    from link.shaper.tc_profiles import apply_profile as tc_apply, clear as tc_clear, get_profiles as tc_get
except Exception as e:  # pragma: no cover
    print(f"[exp] FATAL: cannot import tc_profiles: {e}", file=sys.stderr)
    sys.exit(2)


@dataclass(slots=True)
class ExperimentPlan:
    device_id: str = "rpi5-01"
    iface: str = "eth0"
    both: bool = False            # ingress(ifb0) 포함 여부
    run_root: Path = Path("artifacts/experiments")

    # 시간(초)
    warmup_s: int = 10
    run_s: int = 120
    cooldown_s: int = 5

    # 브로커
    broker: str = "localhost"
    port: int = 1883

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

    # 모드/프로파일
    modes: Tuple[str, ...] = ("periodic", "fixed_tau", "adaptive")  # adaptive는 자리만
    profiles: Tuple[str, ...] = ("slow_10kbps", "delay_loss", "cellular_var")

    # 실행 옵션
    claim_batch: int = 10
    max_inflight: int = 10
    with_collector: bool = False    # collector 프로세스 병행 (수집기 구현 수준에 따라 False 권장)
    tc_var_period_s: Optional[int] = None  # cellular_var 토글 주기(초); None=프로파일 기본


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
        self._procs: List[subprocess.Popen] = []
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

            # 2) edge_daemon 실행
            edge_cmd = self._edge_cmd(sc)
            edge_proc = self._popen(edge_cmd, stdout=log_edge, stderr=subprocess.STDOUT)
            print(f"[exp] edge_daemon pid={edge_proc.pid}")

            # 3) collector (옵션)
            col_proc = None
            if p.with_collector:
                col_cmd = self._collector_cmd(sc)
                if col_cmd:
                    col_proc = self._popen(col_cmd, stdout=log_col, stderr=subprocess.STDOUT)
                    print(f"[exp] collector pid={col_proc.pid}")

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

    def _edge_cmd(self, sc: Scenario) -> List[str]:
        """edge.edge_daemon CLI 인자 구성 (periodic은 τ<0으로 에뮬레이션)."""
        p = self.plan
        run_dir = sc.out_dir
        base = [
            sys.executable, "-m", "edge.edge_daemon",
            "--device-id", p.device_id,
            "--profile", sc.profile.value,
            "--broker", p.broker,
            "--port", str(p.port),
            "--client-id", f"edge-{p.device_id}",
            "--run-dir", str(run_dir),
        ]

        if p.use_mic:
            base += ["--mic-enable",
                     "--mic-sr", str(p.mic_sr),
                     "--mic-frame-ms", str(p.mic_frame_ms),
                     "--mic-alpha", str(p.mic_alpha),
                     "--mic-kbits", str(p.mic_kbits),
                     "--mic-heartbeat", str(p.mic_heartbeat_s)]
            # periodic 모드: resid>τ를 항상 참으로 만들기 위해 τ<0
            mic_tau = (-1e-6) if sc.mode == PolicyMode.PERIODIC else p.mic_tau_fixed
            base += ["--mic-tau", str(mic_tau)]

        if p.use_temp:
            base += ["--temp-enable",
                     "--temp-hz", str(p.temp_hz),
                     "--temp-alpha", str(p.temp_alpha),
                     "--temp-kbits", str(p.temp_kbits),
                     "--temp-heartbeat", str(p.temp_heartbeat_s)]
            temp_tau = (-1e-6) if sc.mode == PolicyMode.PERIODIC else p.temp_tau_fixed
            base += ["--temp-tau", str(temp_tau)]

        if sc.mode == PolicyMode.ADAPTIVE:
            # 아직 edge_daemon에 LinUCB 연결 전 → 명확히 경고하고 fixed_tau와 동일 설정으로 실행 방지
            print("[exp] NOTE: adaptive mode is not yet wired in edge_daemon; skipping this scenario.")
            # None 커맨드 반환 → 상위에서 곧바로 종료/정리
            return [sys.executable, "-c", "import time; print('adaptive mode skipped'); time.sleep(1)"]

        return base

    def _collector_cmd(self, sc: Scenario) -> Optional[List[str]]:
        """
        collector가 모듈로 제공되는 경우에만 실행.
        구현/CLI가 상이할 수 있으므로 최소 인자만 제공(브로커/출력경로).
        """
        try:
            __import__("collector.collector")
        except Exception:
            print("[exp] WARN: collector module not available; skipping collector.")
            return None
        out_path = sc.out_dir / "collector.sqlite"
        return [
            sys.executable, "-m", "collector.collector",
            "--broker", self.plan.broker,
            "--port", str(self.plan.port),
            "--out", str(out_path),
        ]

    # --------- tc 프로파일 적용/해제 ---------

    def _apply_profile(self, profile: LinkProfile) -> None:
        if os.geteuid() != 0:
            print("[exp] WARN: not running as root; tc shaping may fail.", file=sys.stderr)
        varp = self.plan.tc_var_period_s
        tc_apply(self.plan.iface, profile.value, both=self.plan.both, var_period_s=varp)
        self._active_tc = True
        print(f"[exp] tc applied: iface={self.plan.iface} both={self.plan.both} profile={profile.value}")

    def _clear_profile(self) -> None:
        if self._active_tc:
            try:
                tc_clear(self.plan.iface, both=self.plan.both)
            except Exception as e:
                print(f"[exp] WARN: tc clear error: {e}", file=sys.stderr)
            self._active_tc = False
            print("[exp] tc cleared")

    # --------- 유틸 ---------

    def _popen(self, cmd: List[str], **kw) -> subprocess.Popen:
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

def build_scenarios(plan: ExperimentPlan) -> List[Scenario]:
    root = _run_root(plan)
    scenarios: List[Scenario] = []
    for prof_s in plan.profiles:
        lp = LinkProfile(prof_s)
        for mode_s in plan.modes:
            pm = PolicyMode(mode_s)
            name = f"{lp.value}__{pm.value}"
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
        (root / "tc_profiles.json").write_text(json.dumps({k: asdict(v) for k, v in tc_get().items()}, indent=2))
    except Exception:
        pass
    return root


def _utc_ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


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
    try:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], timeout=2).decode().strip()
        info["git_commit"] = sha
    except Exception:
        info["git_commit"] = None
    return info


# ---------- CLI ----------

def parse_args() -> ExperimentPlan:
    ap = argparse.ArgumentParser(description="Run profile×mode matrix experiments (edge_daemon orchestrator)")
    ap.add_argument("--device-id", default="rpi5-01")
    ap.add_argument("--iface", default="eth0")
    ap.add_argument("--both", action="store_true")
    ap.add_argument("--run-root", default="artifacts/experiments")

    ap.add_argument("--warmup-s", type=int, default=10)
    ap.add_argument("--run-s", type=int, default=120)
    ap.add_argument("--cooldown-s", type=int, default=5)

    ap.add_argument("--broker", default="localhost")
    ap.add_argument("--port", type=int, default=1883)

    ap.add_argument("--use-mic", action="store_true")
    ap.add_argument("--use-temp", action="store_true")

    ap.add_argument("--mic-sr", type=int, default=16000)
    ap.add_argument("--mic-frame-ms", type=int, default=100)
    ap.add_argument("--mic-alpha", type=float, default=0.2)
    ap.add_argument("--mic-tau-fixed", type=float, default=3.0)
    ap.add_argument("--mic-kbits", type=int, default=6)
    ap.add_argument("--mic-heartbeat", type=float, default=10.0)

    ap.add_argument("--temp-hz", type=float, default=1.0)
    ap.add_argument("--temp-alpha", type=float, default=0.5)
    ap.add_argument("--temp-tau-fixed", type=float, default=0.2)
    ap.add_argument("--temp-kbits", type=int, default=8)
    ap.add_argument("--temp-heartbeat", type=float, default=10.0)

    ap.add_argument("--modes", default="periodic,fixed_tau,adaptive",
                    help="comma-separated: periodic,fixed_tau,adaptive")
    ap.add_argument("--profiles", default="slow_10kbps,delay_loss,cellular_var",
                    help="comma-separated profile names")
    ap.add_argument("--with-collector", action="store_true")
    ap.add_argument("--tc-var-period", type=int, default=None, help="cellular_var toggle period (seconds)")
    args = ap.parse_args()

    plan = ExperimentPlan(
        device_id=args.device_id,
        iface=args.iface,
        both=bool(args.both),
        run_root=Path(args.run_root),

        warmup_s=args.warmup_s,
        run_s=args.run_s,
        cooldown_s=args.cooldown_s,

        broker=args.broker,
        port=args.port,

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

        modes=tuple(m.strip() for m in args.modes.split(",") if m.strip()),
        profiles=tuple(p.strip() for p in args.profiles.split(",") if p.strip()),
        with_collector=bool(args.with_collector),
        tc_var_period_s=args.tc_var_period if args.tc_var_period is not None else None,
    )
    # 안전장치: 최소 한 센서 활성
    if not plan.use_mic and not plan.use_temp:
        print("[exp] ERROR: enable at least one sensor (--use-mic and/or --use-temp)", file=sys.stderr)
        sys.exit(2)
    # 모드/프로파일 유효성
    for m in plan.modes:
        _ = PolicyMode(m)
    for p in plan.profiles:
        _ = LinkProfile(p)
    return plan


def main():
    plan = parse_args()
    scenarios = build_scenarios(plan)
    runner = ScenarioRunner(plan)
    runner.run_all(scenarios)


if __name__ == "__main__":
    main()

