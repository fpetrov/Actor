import asyncio

import pytest

from actor_runtime.classifiers import ClassifierRegistry
from actor_runtime.errors import ActorError


async def test_async_creation_preserves_existing_sync_factory_options(monkeypatch):
    token = object()
    def factory(**options):
        return options["config"]
    monkeypatch.setattr(ClassifierRegistry, "_factory", staticmethod(lambda _: factory))
    assert ClassifierRegistry.create("fixture", config=token) is token
    assert await ClassifierRegistry.create_async("fixture", config=token) is token


async def test_async_factory_is_awaited_and_sync_entry_refuses_it(monkeypatch):
    token = object()
    async def factory(**options):
        await asyncio.sleep(0)
        return options["config"]
    monkeypatch.setattr(ClassifierRegistry, "_factory", staticmethod(lambda _: factory))
    with pytest.raises(ActorError) as error:
        ClassifierRegistry.create("fixture", config=token)
    assert error.value.code == "classifier_async_required"
    assert await ClassifierRegistry.create_async("fixture", config=token) is token
