from __future__ import annotations

from pathlib import Path

import pytest

from edge.uploader import outbox as outbox_mod


def test_outbox_retry_timeout_and_reset_flow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = {"ns": 1_000_000_000}

    def _now_ns() -> int:
        return int(now["ns"])

    monkeypatch.setattr(outbox_mod.time, "time_ns", _now_ns)

    ob = outbox_mod.Outbox(
        str(tmp_path / "outbox.sqlite"),
        ack_timeout_s=0.05,
        backoff_base_s=0.01,  # constructor clamps to >=50ms by design
        backoff_cap_s=0.05,
    )
    try:
        mid1 = ob.enqueue("edge/dev1/temp/event", b'{"v":1}', qos=1, retain=False)
        mid2 = ob.enqueue("edge/dev1/temp/event", b'{"v":2}', qos=1, retain=False)
        assert ob.pending() == 2

        batch1 = ob.claim_next(limit=1)
        assert [x.id for x in batch1] == [mid1]
        assert ob.ack(mid1) is True

        ds1 = ob.delivery_stats()
        assert ds1.acked == 1
        assert ds1.ack_latency_ms is not None
        assert ds1.ack_latency_ewma_ms is not None

        batch2 = ob.claim_next(limit=1)
        assert [x.id for x in batch2] == [mid2]
        ob.nack(mid2)
        ds2 = ob.delivery_stats()
        assert ds2.nacked == 1
        assert ds2.loss_ewma > 0.0

        # Backoff window not elapsed yet.
        assert ob.claim_next(limit=1) == []

        # base backoff elapsed -> re-claim.
        now["ns"] += 60_000_000
        batch3 = ob.claim_next(limit=1)
        assert [x.id for x in batch3] == [mid2]
        assert batch3[0].attempts >= 2

        # Leave inflight; timeout-based requeue should trigger.
        now["ns"] += 80_000_000
        requeued = ob.requeue_stuck()
        assert requeued == 1
        assert ob.delivery_stats().timeouts == 1

        # Timeout requeue uses configured/clamped backoff (here: 50ms).
        assert ob.claim_next(limit=1) == []
        now["ns"] += 60_000_000
        batch4 = ob.claim_next(limit=1)
        assert [x.id for x in batch4] == [mid2]

        # reset_inflight should move current inflight back to queued.
        restored = ob.reset_inflight()
        assert restored == 1
        batch5 = ob.claim_next(limit=1)
        assert [x.id for x in batch5] == [mid2]
        assert ob.ack(mid2) is True

        st = ob.stats()
        assert st["total"] == 0
        assert st["queued"] == 0
        assert st["inflight"] == 0
    finally:
        ob.close()
