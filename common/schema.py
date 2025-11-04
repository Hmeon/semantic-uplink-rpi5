"""공통 메시지 스키마 정의 (msgspec)."""
from typing import Optional, Literal
import msgspec

SensorName = Literal["mic", "temp"]

class Event(msgspec.Struct, frozen=True):
    """의미전송 이벤트 페이로드 (가독성을 위해 JSON → 추후 CBOR 전환 가능)."""
    ts: str
    seq: int
    device_id: str
    sensor: SensorName
    val: float
    pred: float
    res: float
    tau: float
    kbits: int
    aoi_ms: Optional[float] = None
    profile: Optional[str] = None
    policy: Optional[str] = None

class PolicyDecision(msgspec.Struct, frozen=True):
    ts: str
    device_id: str
    state_aoi: float
    state_res: float
    state_res_var: float
    state_loss: float
    state_q_len: int
    tau: float
    kbits: int
    reward: float
