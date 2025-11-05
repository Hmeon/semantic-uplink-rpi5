# link/shaper/tc_profiles.py
# Python 3.10+
# 목적: 재현 가능한 링크 제약(저속/손실/지연)을 tc(HTB+netem)로 적용/해제/조회
# - root 권한 필요. egress(기본) / ingress(ifb0 리다이렉션) 지원(both=True).
# - 프로파일: slow_10kbps, delay_loss, cellular_var(50↔200kbps 토글)  [과제 제안서와 정합]  # noqa
# - 안전성: apply는 replace 사용, clear는 존재하지 않아도 오류 없이 진행, SIGINT에서도 원복을 권장.

from __future__ import annotations

import argparse
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional

# ---------- 프로파일 정의 (동결안과 동일) ----------
@dataclass(frozen=True)
class TcProfile:
    name: str
    # egress 기준 속도(kbit); cellular_var는 None (토글 사용)
    rate_kbit: Optional[int]
    delay_ms: int
    jitter_ms: int
    loss_pct: float
    reorder_pct: float = 0.0
    # cellular_var 전용
    low_kbit: Optional[int] = None
    high_kbit: Optional[int] = None
    var_default_period_s: int = 30

PROFILES: Dict[str, TcProfile] = {
    # 10kbps, delay 300±50ms, loss 3%
    "slow_10kbps": TcProfile(
        name="slow_10kbps", rate_kbit=10, delay_ms=300, jitter_ms=50, loss_pct=3.0, reorder_pct=0.0
    ),
    # 100kbps, delay 500±100ms, loss 8%, reorder 10%
    "delay_loss": TcProfile(
        name="delay_loss", rate_kbit=100, delay_ms=500, jitter_ms=100, loss_pct=8.0, reorder_pct=10.0
    ),
    # 50↔200kbps 변동, delay 120±80ms, loss 2%
    "cellular_var": TcProfile(
        name="cellular_var", rate_kbit=None, delay_ms=120, jitter_ms=80, loss_pct=2.0,
        reorder_pct=0.0, low_kbit=50, high_kbit=200, var_default_period_s=30
    ),
}


# ---------- 내부 유틸 ----------

def _require_root():
    if os.geteuid() != 0:
        raise PermissionError("tc_profiles requires root privileges (run as root).")

def _run(cmd: str, check: bool = True) -> subprocess.CompletedProcess:
    # 모든 명령은 쉘 로그에 보이도록 출력
    print(f"[tc] $ {cmd}")
    return subprocess.run(shlex.split(cmd), capture_output=True, text=True, check=check)

def _ignore(cmd: str) -> None:
    try:
        _run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        # 존재하지 않음 등은 무시하고 진행
        sys.stderr.write(f"[tc] ignore: {cmd} -> {e.returncode}\n")

def _build_netem_args(p: TcProfile) -> str:
    # delay±jitter, loss, reorder를 조합
    parts = []
    if p.delay_ms > 0:
        if p.jitter_ms > 0:
            parts += ["delay", f"{p.delay_ms}ms", f"{p.jitter_ms}ms", "distribution", "normal"]
        else:
            parts += ["delay", f"{p.delay_ms}ms"]
    if p.loss_pct > 0:
        parts += ["loss", "random", f"{p.loss_pct}%"]
    if p.reorder_pct and p.reorder_pct > 0:
        # reorder는 delay와 함께 사용하는 것이 일반적
        parts += ["reorder", f"{p.reorder_pct}%"]
    return " ".join(parts)


# ---------- Egress 설치(HTB+netem) ----------

def _apply_egress(iface: str, rate_kbit: int, netem_args: str) -> None:
    # HTB 루트 및 클래스(1:1), leaf에 netem 부착
    _run(f"tc qdisc replace dev {iface} root handle 1: htb default 1")
    _run(f"tc class replace dev {iface} parent 1: classid 1:1 htb rate {rate_kbit}kbit ceil {rate_kbit}kbit")
    if netem_args:
        _run(f"tc qdisc replace dev {iface} parent 1:1 handle 10: netem {netem_args}")
    else:
        # netem 미사용 시에도 leaf qdisc를 명시적으로 fifo로 설정
        _run(f"tc qdisc replace dev {iface} parent 1:1 handle 10: pfifo limit 1000")

# ---------- Ingress 설치(ifb0 경유) ----------

def _ensure_ifb(ifb: str = "ifb0") -> None:
    # ifb 모듈 및 디바이스 준비
    _ignore("modprobe ifb numifbs=1")
    _ignore(f"ip link add {ifb} type ifb")
    _ignore(f"ip link set dev {ifb} up")

def _apply_ingress(iface: str, rate_kbit: int, netem_args: str, ifb: str = "ifb0") -> None:
    _ensure_ifb(ifb)
    # ingress qdisc를 통해 트래픽을 ifb로 redirect
    _run(f"tc qdisc replace dev {iface} ingress")
    # 중복 필터 추가 방지 위해 기존 필터 제거 후 다시 추가
    _ignore(f"tc filter del dev {iface} parent ffff:")
    _run(
        f"tc filter add dev {iface} parent ffff: protocol all u32 match u32 0 0 "
        f"action mirred egress redirect dev {ifb}"
    )
    # ifb0에 egress와 동일하게 HTB+netem 설치
    _apply_egress(ifb, rate_kbit, netem_args)

# ---------- 토글 스레드(셀룰러 변동 속도) ----------

class _RateToggle:
    def __init__(self, iface: str, ifb: Optional[str], low_kbit: int, high_kbit: int, period_s: int):
        self.iface = iface
        self.ifb = ifb
        self.low_kbit = low_kbit
        self.high_kbit = high_kbit
        self.period_s = max(2, period_s)
        self._stop = threading.Event()
        self._th: Optional[threading.Thread] = None
        self._cur = "low"  # start low

    def start(self):
        def _loop():
            while not self._stop.wait(self.period_s):
                try:
                    self._flip()
                except Exception as e:
                    sys.stderr.write(f"[tc] toggle error: {e}\n")
        self._th = threading.Thread(target=_loop, daemon=True)
        self._th.start()
        print(f"[tc] rate toggle started: {self.low_kbit}↔{self.high_kbit} kbit every {self.period_s}s")

    def _flip(self):
        self._cur = "high" if self._cur == "low" else "low"
        rate = self.high_kbit if self._cur == "high" else self.low_kbit
        # egress 변경
        _run(f"tc class replace dev {self.iface} parent 1: classid 1:1 htb rate {rate}kbit ceil {rate}kbit")
        # ingress(ifb) 변경
        if self.ifb:
            _run(f"tc class replace dev {self.ifb} parent 1: classid 1:1 htb rate {rate}kbit ceil {rate}kbit")
        print(f"[tc] toggled rate -> {rate} kbit")

    def stop(self, timeout: float = 2.0):
        self._stop.set()
        if self._th and self._th.is_alive():
            self._th.join(timeout=timeout)
        print("[tc] rate toggle stopped.")


# ---------- 외부 API ----------

_toggle_registry: Dict[str, _RateToggle] = {}  # iface -> toggle

def get_profiles() -> Dict[str, TcProfile]:
    return dict(PROFILES)

def apply_profile(iface: str, profile: str, both: bool = False, var_period_s: int | None = None) -> None:
    """
    지정 인터페이스(iface)에 프로파일을 적용한다.
    both=True면 ifb0를 사용해 ingress에도 동일 제약을 건다.
    """
    _require_root()
    if profile not in PROFILES:
        raise ValueError(f"unknown profile: {profile} (choices: {', '.join(PROFILES)})")
    p = PROFILES[profile]
    netem_args = _build_netem_args(p)

    # egress
    if p.rate_kbit is not None:
        _apply_egress(iface, p.rate_kbit, netem_args)
    else:
        # cellular_var: 초기값 low로 설정
        _apply_egress(iface, p.low_kbit, netem_args)

    # ingress (옵션)
    ifb_name = None
    if both:
        if p.rate_kbit is not None:
            _apply_ingress(iface, p.rate_kbit, netem_args, ifb="ifb0")
        else:
            _apply_ingress(iface, p.low_kbit, netem_args, ifb="ifb0")
        ifb_name = "ifb0"

    # 토글 스레드 관리
    # 동일 iface에 기존 토글이 있으면 정지 후 교체
    if iface in _toggle_registry:
        _toggle_registry[iface].stop()
        _toggle_registry.pop(iface, None)

    if p.name == "cellular_var":
        period = var_period_s if var_period_s and var_period_s > 0 else p.var_default_period_s
        toggler = _RateToggle(iface=iface, ifb=ifb_name, low_kbit=p.low_kbit, high_kbit=p.high_kbit, period_s=period)
        toggler.start()
        _toggle_registry[iface] = toggler

def clear(iface: str, both: bool = False) -> None:
    """적용한 제약을 제거하고 시스템 기본 상태로 원복한다."""
    _require_root()
    # 토글 스레드 정지
    if iface in _toggle_registry:
        try:
            _toggle_registry[iface].stop()
        finally:
            _toggle_registry.pop(iface, None)

    # egress 원복
    _ignore(f"tc qdisc del dev {iface} root")

    # ingress(ifb0) 원복
    if both:
        _ignore(f"tc qdisc del dev {iface} ingress")
        _ignore(f"tc filter del dev {iface} parent ffff:")
        # ifb0 정리
        _ignore("tc qdisc del dev ifb0 root")
        _ignore("ip link set dev ifb0 down")
        _ignore("ip link delete ifb0 type ifb")

def status(iface: str, both: bool = False) -> str:
    """현재 qdisc/class 구성을 문자열로 반환."""
    _require_root()
    out = []
    q = _run(f"tc qdisc show dev {iface}", check=False)
    c = _run(f"tc class show dev {iface}", check=False)
    out.append(f"# {iface} qdisc:\n{q.stdout}\n# {iface} class:\n{c.stdout}")
    if both:
        q2 = _run("tc qdisc show dev ifb0", check=False)
        c2 = _run("tc class show dev ifb0", check=False)
        out.append(f"# ifb0 qdisc:\n{q2.stdout}\n# ifb0 class:\n{c2.stdout}")
    return "\n".join(out)


# ---------- CLI ----------

def _install_signal_handlers(iface: str, both: bool):
    def _handler(signum, frame):
        print(f"[tc] signal={signum} -> clearing profile and exit")
        try:
            clear(iface, both=both)
        finally:
            sys.exit(0)

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)


def main():
    parser = argparse.ArgumentParser(description="tc profile applier (HTB+netem)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_apply = sub.add_parser("apply", help="apply a link profile")
    p_apply.add_argument("--iface", required=True)
    p_apply.add_argument("--profile", required=True, choices=list(PROFILES.keys()))
    p_apply.add_argument("--both", action="store_true", help="apply to ingress via ifb0 as well")
    p_apply.add_argument("--var-period", type=int, default=None, help="period(s) for cellular_var toggle")

    p_clear = sub.add_parser("clear", help="clear shaper")
    p_clear.add_argument("--iface", required=True)
    p_clear.add_argument("--both", action="store_true")

    p_status = sub.add_parser("status", help="show current qdisc/class")
    p_status.add_argument("--iface", required=True)
    p_status.add_argument("--both", action="store_true")

    args = parser.parse_args()
    _install_signal_handlers(iface=getattr(args, "iface", ""), both=getattr(args, "both", False))

    try:
        if args.cmd == "apply":
            apply_profile(args.iface, args.profile, both=args.both, var_period_s=args.var_period)
            print("[tc] profile applied. (Ctrl+C to clear)")
            # apply 모드로 실행된 경우, 셀룰러 토글이 있다면 유지 대기
            if args.profile == "cellular_var":
                # 토글 스레드가 동작하는 동안 프로세스 유지
                while True:
                    time.sleep(1)
        elif args.cmd == "clear":
            clear(args.iface, both=args.both)
            print("[tc] cleared.")
        elif args.cmd == "status":
            print(status(args.iface, both=args.both))
    except PermissionError as e:
        sys.stderr.write(str(e) + "\n")
        sys.exit(1)
    except Exception as e:
        sys.stderr.write(f"[tc] error: {e}\n")
        sys.exit(2)


if __name__ == "__main__":
    main()
