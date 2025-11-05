import pytest


@pytest.mark.asyncio
async def test_outbox_api_exists():
    from edge.uploader.outbox import Outbox
    ob = Outbox("outbox.db")
    # Methods exist
    assert hasattr(ob, "setup")
    assert hasattr(ob, "enqueue")
    assert hasattr(ob, "pending")
    assert hasattr(ob, "mark_done")
