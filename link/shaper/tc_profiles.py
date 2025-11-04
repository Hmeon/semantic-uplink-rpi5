"""tc/netem/tbf 프로파일 적용/해제 스켈레톤."""
from __future__ import annotations

PROFILES = {
    "slow_10kbps": ["tbf rate 10kbit burst 4kbit limit 4k", "netem delay 300ms loss 3%"],
    "delay_loss":  ["tbf rate 100kbit burst 16kbit limit 32k", "netem delay 500ms loss 8% reorder 10%"],
    "cellular_var":["tbf rate 200kbit burst 32kbit limit 64k", "netem delay 120ms loss 2%"],
}

def apply(dev: str = "lo", profile: str = "slow_10kbps") -> None:
    """TODO: subprocess로 qdisc add/del 실행, 에러 처리/권한 확인."""
    pass

def clear(dev: str = "lo") -> None:
    """TODO: qdisc del root."""
    pass
