"""MQTT 연결/발행/구독 헬퍼 스켈레톤."""
# common/mqttutil.py
# Python 3.10+
# MQTT v3.1.1 PUBLISH 패킷의 총 인코딩 바이트 수(브로커 수신 기준)를 정확히 계산한다.
# - Rate 산출은 "헤더 포함 브로커 수신 바이트/초" 기준이므로 본 함수를 표준으로 사용한다.  [과제 제안서 KPI 정의와 정합]  # noqa
# - QoS0/1/2 지원. Remaining Length(가변 길이) 바이트 수를 정확히 반영.
# - PUBLISH 토픽명 검증: 비어있지 않음, 와일드카드(+/#) 금지, UTF-8 바이트 길이 ≤ 65535.
# - QoS0에서 DUP=True는 MQTT 3.1.1 규격상 허용되지 않으므로 에러 처리.

from __future__ import annotations

from typing import Final

__all__ = ["mqtt_v311_publish_size"]

# MQTT v3.1.1 Remaining Length 한계 (268,435,455 = 256 MB - 1)
_MQTT_REMAINING_LENGTH_MAX: Final[int] = 268_435_455


def _remaining_length_num_bytes(x: int) -> int:
    """MQTT v3.1.1 'Remaining Length' 필드의 인코딩 바이트 수를 반환한다."""
    if x < 0 or x > _MQTT_REMAINING_LENGTH_MAX:
        raise ValueError(
            f"remaining length out of range: {x} (0..{_MQTT_REMAINING_LENGTH_MAX})"
        )
    # 7비트 단위 가변 인코딩(최대 4바이트)
    if x <= 127:
        return 1
    elif x <= 16_383:
        return 2
    elif x <= 2_097_151:
        return 3
    else:
        return 4


def _validate_publish_topic(topic: str) -> int:
    """
    PUBLISH 패킷의 Topic Name 제약을 검증하고 UTF-8 바이트 길이를 반환한다.
    - 빈 문자열 금지
    - 와일드카드 '+', '#' 금지 (PUBLISH 토픽에서는 사용할 수 없음)
    - UTF-8 바이트 길이 ≤ 65535 (2바이트 길이 필드)
    """
    if not isinstance(topic, str):
        raise TypeError("topic must be str")
    if topic == "":
        raise ValueError("PUBLISH topic must be non-empty")
    if "+" in topic or "#" in topic:
        raise ValueError("PUBLISH topic MUST NOT contain wildcards '+' or '#'")
    try:
        b = topic.encode("utf-8")
    except UnicodeEncodeError as e:
        raise ValueError(f"topic must be valid UTF-8: {e}") from e
    if len(b) > 0xFFFF:
        raise ValueError(f"topic too long: {len(b)} bytes (max 65535)")
    return len(b)


def mqtt_v311_publish_size(
    topic: str,
    payload_len: int,
    qos: int = 1,
    dup: bool = False,
    retain: bool = False,
) -> int:
    """
    MQTT 3.1.1 PUBLISH 패킷의 총 바이트 수(고정헤더 + 가변헤더 + 페이로드)를 반환한다.

    Parameters
    ----------
    topic : str
        PUBLISH Topic Name (UTF-8). 와일드카드(+/#) 금지, 빈 문자열 금지.
    payload_len : int
        페이로드의 바이트 수. 문자열을 보낼 경우 반드시 UTF-8로 인코딩한 bytes 길이를 사용.
    qos : int, default 1
        0, 1, 2만 허용. QoS>0 일 때만 Packet Identifier(2바이트)가 포함된다.
    dup : bool, default False
        고정헤더 플래그(DUP). QoS 0에서는 DUP=True가 허용되지 않으므로 에러.
        (크기에는 영향 없음: 플래그는 1바이트 안에서 비트만 변경)
    retain : bool, default False
        RETAIN 플래그. 크기에는 영향 없음.

    Returns
    -------
    int
        총 인코딩 바이트 수.

    Notes
    -----
    총 길이 = FixedHeader(1바이트 + RemainingLength 가변바이트) +
             VariableHeader(TopicNameLen 2바이트 + TopicName + [PacketId 2바이트 if QoS>0]) +
             Payload(payload_len)

    RemainingLength = VariableHeader + Payload 의 합이며, 값에 따라 1~4바이트로 인코딩된다.
    """
    # --- 입력 검증 ---
    topic_len = _validate_publish_topic(topic)

    if not isinstance(payload_len, int):
        raise TypeError("payload_len must be int (bytes length)")
    if payload_len < 0:
        raise ValueError("payload_len must be non-negative")

    if qos not in (0, 1, 2):
        raise ValueError("qos must be 0, 1, or 2")
    if qos == 0 and dup:
        # MQTT 3.1.1: QoS 0 메시지는 DUP 비트가 0이어야 함.
        raise ValueError("DUP flag must be False when QoS is 0")

    # --- 가변 헤더 길이 계산 ---
    var_header_len = 2 + topic_len  # Topic Name length(2) + Topic Name bytes
    if qos > 0:
        var_header_len += 2  # Packet Identifier

    # --- Remaining Length 및 고정 헤더 ---
    remaining = var_header_len + payload_len
    fixed_header_len = 1 + _remaining_length_num_bytes(remaining)  # 1=Control byte

    # 총 바이트 수
    total = fixed_header_len + remaining
    return total
