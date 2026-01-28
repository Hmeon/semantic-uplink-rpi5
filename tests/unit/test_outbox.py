import pytest


@pytest.mark.asyncio
async def test_outbox_api_exists(tmp_path):
    from edge.uploader.outbox import Outbox
    ob = Outbox(str(tmp_path / "outbox.sqlite"))
    # Methods exist
    assert hasattr(ob, "setup")
    assert hasattr(ob, "enqueue")
    assert hasattr(ob, "pending")
    assert hasattr(ob, "mark_done")
    ob.close()


def test_outbox_ack_latency_tracks_event_topics(tmp_path):
    import time

    from edge.uploader.outbox import Outbox

    ob = Outbox(str(tmp_path / "outbox.sqlite"))
    try:
        created_ns = time.time_ns() - 50_000_000  # 50ms ago
        mid = ob.enqueue(
            "custom/dev1/temp/event",
            b"{}",
            qos=1,
            retain=False,
            created_ns=created_ns,
        )
        assert ob.ack(mid) is True

        stats = ob.delivery_stats()
        assert stats.ack_latency_ms is not None
        assert float(stats.ack_latency_ms) > 1.0
        assert stats.ack_latency_ewma_ms is not None
    finally:
        ob.close()
