# link/shaper/tc_profiles.py
# Python 3.10+
# 목적: 재현 가능한 링크 제약(저속/손실/지연)을 tc(HTB+netem)로 적용/해제/조회
# - root 권한 필요. egress(기본) / ingress(ifb0 리다이렉션) 지원(both=True).
# - 프로파일: slow_10kbps, delay_loss, cellular_var(50↔200kbps 토글), lora_sf10/lora_sf12(LoRa-like)  # noqa
# - 안전성: apply는 replace 사용, clear는 존재하지 않아도 오류 없이 진행, SIGINT에서도 원복을 권장.

"""Apply predefined tc/netem link shaping profiles.

Provides utilities to apply/clear/query Linux tc shaping profiles with optional
ingress support via ifb. Requires root or CAP_NET_ADMIN for any tc operations.
"""

from __future__ import annotations

import argparse
import logging
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from common.config import load_link_profiles_config
from common.logging_setup import add_logging_cli_args, setup_logging_from_args

logger = logging.getLogger(__name__)


# ---------- 프로파일 정의 (동결안과 동일) ----------
@dataclass(frozen=True)
class TcProfile:
    """Definition of a tc/netem shaping profile.

    Args:
        name: Profile name identifier.
        rate_kbit: Egress rate in kbit; None enables variable mode.
        delay_ms: Base delay in milliseconds.
        jitter_ms: Jitter in milliseconds.
        loss_pct: Loss percentage (0..100).
        loss_corr_pct: Loss correlation percentage (0..100).
        reorder_pct: Reorder percentage (0..100).
        low_kbit: Low rate for variable mode.
        high_kbit: High rate for variable mode.
        var_default_period_s: Toggle period in seconds for variable mode.

    Returns:
        None.

    Raises:
        None.

    Side Effects:
        - None.

    Contract:
        - Fields mirror LinkProfileConfig.

    Failure Modes:
        - Invalid values surface when applying profiles.
    """
    name: str
    # egress 기준 속도(kbit); cellular_var는 None (토글 사용)
    rate_kbit: int | None
    delay_ms: int
    jitter_ms: int
    loss_pct: float
    loss_corr_pct: float = 0.0
    reorder_pct: float = 0.0
    # cellular_var 전용
    low_kbit: int | None = None
    high_kbit: int | None = None
    var_default_period_s: int = 30

PROFILES: dict[str, TcProfile] = {
    # 10kbps, delay 300±50ms, loss 3%
    "slow_10kbps": TcProfile(
        name="slow_10kbps",
        rate_kbit=10,
        delay_ms=300,
        jitter_ms=50,
        loss_pct=3.0,
        loss_corr_pct=0.0,
        reorder_pct=0.0,
    ),
    # 100kbps, delay 500±100ms, loss 8%, reorder 10%
    "delay_loss": TcProfile(
        name="delay_loss",
        rate_kbit=100,
        delay_ms=500,
        jitter_ms=100,
        loss_pct=8.0,
        loss_corr_pct=0.0,
        reorder_pct=10.0,
    ),
    # 50↔200kbps 변동, delay 120±80ms, loss 2%
    "cellular_var": TcProfile(
        name="cellular_var",
        rate_kbit=None,
        delay_ms=120,
        jitter_ms=80,
        loss_pct=2.0,
        loss_corr_pct=0.0,
        reorder_pct=0.0,
        low_kbit=50,
        high_kbit=200,
        var_default_period_s=30,
    ),
    # LoRa-like (very low rate, long delay, high + bursty loss)
    # NOTE: LoRaWAN's duty-cycle/ACK constraints are not modeled; this is an IP-level approximation.
    "lora_sf10": TcProfile(
        name="lora_sf10",
        rate_kbit=5,
        delay_ms=1200,
        jitter_ms=400,
        loss_pct=10.0,
        loss_corr_pct=40.0,
        reorder_pct=0.0,
    ),
    "lora_sf12": TcProfile(
        name="lora_sf12",
        rate_kbit=2,
        delay_ms=2000,
        jitter_ms=600,
        loss_pct=15.0,
        loss_corr_pct=50.0,
        reorder_pct=0.0,
    ),
}

def load_profiles_config(path: str | os.PathLike) -> dict[str, TcProfile]:
    """Load tc profiles from a YAML config file.

    Args:
        path: Path to link profiles YAML.

    Returns:
        Mapping of profile name to TcProfile.

    Raises:
        FileNotFoundError: If the YAML file does not exist.
        ValueError: If YAML parsing or validation fails.
        TypeError: If the YAML root is not a mapping.

    Side Effects:
        - Reads the YAML file from disk.

    Contract:
        - Uses LinkProfilesConfig validation.

    Failure Modes:
        - Validation errors propagate to the caller.
    """
    cfg = load_link_profiles_config(Path(path))
    out: dict[str, TcProfile] = {}
    for name, p in cfg.profiles.items():
        out[str(name)] = TcProfile(
            name=str(name),
            rate_kbit=None if p.rate_kbit is None else int(p.rate_kbit),
            delay_ms=int(p.delay_ms),
            jitter_ms=int(p.jitter_ms),
            loss_pct=float(p.loss_pct),
            loss_corr_pct=float(p.loss_corr_pct),
            reorder_pct=float(p.reorder_pct),
            low_kbit=None if p.low_kbit is None else int(p.low_kbit),
            high_kbit=None if p.high_kbit is None else int(p.high_kbit),
            var_default_period_s=int(p.var_default_period_s),
        )
    return out


# ---------- 내부 유틸 ----------

def _require_root():
    geteuid = getattr(os, "geteuid", None)
    if geteuid is None:
        raise OSError("tc/netem is supported on Linux only (os.geteuid unavailable).")

    # Prefer capability-based checks so systemd can run this as a non-root user with
    # `AmbientCapabilities=CAP_NET_ADMIN` (safer than running the full stack as root).
    if geteuid() == 0:
        return

    cap_eff = 0
    try:
        with open("/proc/self/status", encoding="utf-8") as f:
            for line in f:
                if line.startswith("CapEff:"):
                    _, hexv = line.split(":", 1)
                    cap_eff = int(hexv.strip(), 16)
                    break
    except Exception:
        cap_eff = 0

    cap_net_admin = 12
    if (cap_eff >> cap_net_admin) & 1:
        return

    raise PermissionError(
        "tc_profiles requires CAP_NET_ADMIN (run as root or grant CAP_NET_ADMIN to the process)."
    )

def _run(cmd: str, check: bool = True) -> subprocess.CompletedProcess:
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug("tc_cmd %s", cmd)
    return subprocess.run(shlex.split(cmd), capture_output=True, text=True, check=check)

def _ignore(cmd: str) -> None:
    try:
        _run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        # 존재하지 않음 등은 무시하고 진행
        logger.warning("tc_ignore cmd=%s rc=%s", cmd, e.returncode)

def _build_netem_args(p: TcProfile) -> str:
    # delay±jitter, loss, reorder를 조합
    parts = []
    if p.delay_ms > 0:
        if p.jitter_ms > 0:
            parts += ["delay", f"{p.delay_ms}ms", f"{p.jitter_ms}ms", "distribution", "normal"]
        else:
            parts += ["delay", f"{p.delay_ms}ms"]
    if p.loss_pct > 0:
        if p.loss_corr_pct and p.loss_corr_pct > 0:
            parts += ["loss", "random", f"{p.loss_pct}%", f"{p.loss_corr_pct}%"]
        else:
            parts += ["loss", "random", f"{p.loss_pct}%"]
    if p.reorder_pct and p.reorder_pct > 0:
        # reorder는 delay와 함께 사용하는 것이 일반적
        parts += ["reorder", f"{p.reorder_pct}%"]
    return " ".join(parts)


# ---------- Egress 설치(HTB+netem) ----------

def _apply_egress(iface: str, rate_kbit: int, netem_args: str) -> None:
    # HTB 루트 및 클래스(1:1), leaf에 netem 부착
    _run(f"tc qdisc replace dev {iface} root handle 1: htb default 1")
    _run(
        f"tc class replace dev {iface} parent 1: classid 1:1 "
        f"htb rate {rate_kbit}kbit ceil {rate_kbit}kbit"
    )
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
    def __init__(self, iface: str, ifb: str | None, low_kbit: int, high_kbit: int, period_s: int):
        self.iface = iface
        self.ifb = ifb
        self.low_kbit = low_kbit
        self.high_kbit = high_kbit
        self.period_s = max(2, period_s)
        self._stop = threading.Event()
        self._th: threading.Thread | None = None
        self._cur = "low"  # start low

    def start(self):
        def _loop():
            while not self._stop.wait(self.period_s):
                try:
                    self._flip()
                except Exception:
                    logger.exception("tc_rate_toggle_error iface=%s", self.iface)
        self._th = threading.Thread(target=_loop, daemon=True)
        self._th.start()
        logger.info(
            "tc_rate_toggle_started iface=%s low_kbit=%s high_kbit=%s period_s=%s",
            self.iface,
            int(self.low_kbit),
            int(self.high_kbit),
            int(self.period_s),
        )

    def _flip(self):
        self._cur = "high" if self._cur == "low" else "low"
        rate = self.high_kbit if self._cur == "high" else self.low_kbit
        # egress 변경
        _run(
            f"tc class replace dev {self.iface} parent 1: classid 1:1 "
            f"htb rate {rate}kbit ceil {rate}kbit"
        )
        # ingress(ifb) 변경
        if self.ifb:
            _run(
                f"tc class replace dev {self.ifb} parent 1: classid 1:1 "
                f"htb rate {rate}kbit ceil {rate}kbit"
            )
        logger.info("tc_rate_toggled iface=%s rate_kbit=%s", self.iface, int(rate))

    def stop(self, timeout: float = 2.0):
        self._stop.set()
        if self._th and self._th.is_alive():
            self._th.join(timeout=timeout)
        logger.info("tc_rate_toggle_stopped iface=%s", self.iface)


# ---------- 외부 API ----------

_toggle_registry: dict[str, _RateToggle] = {}  # iface -> toggle

def get_profiles() -> dict[str, TcProfile]:
    """Return built-in tc profile definitions.

    Args:
        None.

    Returns:
        Copy of the built-in profile mapping.

    Raises:
        None.

    Side Effects:
        - None.

    Contract:
        - Returned mapping is a shallow copy.

    Failure Modes:
        - None.
    """
    return dict(PROFILES)

def apply_profile(
    iface: str,
    profile: str,
    both: bool = False,
    var_period_s: int | None = None,
    profiles: dict[str, TcProfile] | None = None,
) -> None:
    """Apply a tc/netem profile to an interface.

    Args:
        iface: Network interface to shape.
        profile: Profile name to apply.
        both: Apply to ingress via ifb when True.
        var_period_s: Optional toggle period for cellular_var profiles.
        profiles: Optional profile mapping override.

    Returns:
        None.

    Raises:
        PermissionError: If CAP_NET_ADMIN/root is missing.
        ValueError: If the profile name is unknown.
        OSError: If tc commands fail to execute.

    Side Effects:
        - Executes tc/ip commands to modify qdisc settings.
        - Starts or replaces a rate toggle thread for variable profiles.

    Contract:
        - Egress shaping is always applied; ingress is optional.

    Failure Modes:
        - Command failures propagate as exceptions.
    """
    _require_root()
    profiles_map = profiles if profiles is not None else PROFILES
    if profile not in profiles_map:
        choices = ", ".join(profiles_map)
        raise ValueError(f"unknown profile: {profile} (choices: {choices})")
    p = profiles_map[profile]
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
        period = (
            var_period_s if var_period_s and var_period_s > 0 else p.var_default_period_s
        )
        toggler = _RateToggle(
            iface=iface,
            ifb=ifb_name,
            low_kbit=p.low_kbit,
            high_kbit=p.high_kbit,
            period_s=period,
        )
        toggler.start()
        _toggle_registry[iface] = toggler

def clear(iface: str, both: bool = False) -> None:
    """Clear tc/netem shaping and restore default state.

    Args:
        iface: Network interface to clear.
        both: Clear ingress shaping via ifb when True.

    Returns:
        None.

    Raises:
        PermissionError: If CAP_NET_ADMIN/root is missing.
        OSError: If tc commands fail to execute.

    Side Effects:
        - Executes tc/ip commands to remove qdisc settings.
        - Stops any active rate toggle threads.

    Contract:
        - Ignores missing qdisc/interfaces via best-effort cleanup.

    Failure Modes:
        - Command failures propagate as exceptions.
    """
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
    """Return current tc qdisc/class configuration as text.

    Args:
        iface: Network interface to query.
        both: Include ingress (ifb) status when True.

    Returns:
        Human-readable qdisc/class output.

    Raises:
        PermissionError: If CAP_NET_ADMIN/root is missing.
        OSError: If tc commands fail to execute.

    Side Effects:
        - Executes tc commands to fetch status.

    Contract:
        - Returns raw command output for debugging.

    Failure Modes:
        - Command failures propagate as exceptions.
    """
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
        logger.info("tc_signal=%s clearing_profile iface=%s both=%s", signum, iface, both)
        try:
            clear(iface, both=both)
        finally:
            sys.exit(0)

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)


def main():
    """CLI entry point for tc profile application.

    Args:
        None.

    Returns:
        None.

    Raises:
        SystemExit: If CLI arguments are invalid or tc operations fail.

    Side Effects:
        - Applies/clears/query tc profiles via system commands.

    Contract:
        - Requires CAP_NET_ADMIN/root for tc operations.

    Failure Modes:
        - Exits with non-zero status on command failures.
    """
    parser = argparse.ArgumentParser(description="tc profile applier (HTB+netem)")
    add_logging_cli_args(parser)
    parser.add_argument(
        "--profiles-config",
        default=None,
        help="YAML path for overriding profiles (e.g., configs/link_profiles.yaml)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_apply = sub.add_parser("apply", help="apply a link profile")
    p_apply.add_argument("--iface", required=True)
    p_apply.add_argument("--profile", required=True, choices=list(PROFILES.keys()))
    p_apply.add_argument("--both", action="store_true", help="apply to ingress via ifb0 as well")
    p_apply.add_argument(
        "--var-period",
        type=int,
        default=None,
        help="period(s) for cellular_var toggle",
    )

    p_clear = sub.add_parser("clear", help="clear shaper")
    p_clear.add_argument("--iface", required=True)
    p_clear.add_argument("--both", action="store_true")

    p_status = sub.add_parser("status", help="show current qdisc/class")
    p_status.add_argument("--iface", required=True)
    p_status.add_argument("--both", action="store_true")

    args = parser.parse_args()
    setup_logging_from_args(args)
    _install_signal_handlers(iface=getattr(args, "iface", ""), both=getattr(args, "both", False))

    profiles_override = None
    if args.profiles_config:
        try:
            profiles_override = load_profiles_config(args.profiles_config)
        except Exception:
            logger.exception("failed to load profiles_config=%s", args.profiles_config)
            profiles_override = None

    try:
        if args.cmd == "apply":
            apply_profile(
                args.iface,
                args.profile,
                both=args.both,
                var_period_s=args.var_period,
                profiles=profiles_override,
            )
            logger.info(
                "tc_profile_applied iface=%s profile=%s both=%s",
                args.iface,
                args.profile,
                bool(args.both),
            )
            # apply 모드로 실행된 경우, 셀룰러 토글이 있다면 유지 대기
            if args.profile == "cellular_var":
                # 토글 스레드가 동작하는 동안 프로세스 유지
                while True:
                    time.sleep(1)
        elif args.cmd == "clear":
            clear(args.iface, both=args.both)
            logger.info("tc_cleared iface=%s both=%s", args.iface, bool(args.both))
        elif args.cmd == "status":
            logger.info("\n%s", status(args.iface, both=args.both))
    except PermissionError as e:
        logger.error("%s", e)
        sys.exit(1)
    except Exception:
        logger.exception("tc_profiles error")
        sys.exit(2)


if __name__ == "__main__":
    main()
