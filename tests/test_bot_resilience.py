import pytest

from app.bot import dispatcher as dispatcher_module


def test_global_error_handler_is_registered():
    handlers = dispatcher_module.dp.errors.handlers
    callbacks = [getattr(h, "callback", h) for h in handlers]
    assert dispatcher_module.on_handler_error in callbacks


@pytest.mark.asyncio
async def test_global_error_handler_returns_true_without_update():
    class _FakeEvent:
        update = None
        exception = RuntimeError("boom")

    assert await dispatcher_module.on_handler_error(_FakeEvent()) is True
