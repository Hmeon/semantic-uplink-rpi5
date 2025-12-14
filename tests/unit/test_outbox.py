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
