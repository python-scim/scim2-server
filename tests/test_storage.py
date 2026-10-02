import asyncio

from scim2_server.storage import AsyncScimStorage
from scim2_server.storage import ScimStorage


class MinimalStorage(ScimStorage):
    def get(self, resource_type, resource_id):
        raise NotImplementedError

    def search(self, resource_types, search_request):
        raise NotImplementedError

    def create(self, resource_type, resource):
        raise NotImplementedError

    def update(self, resource_type, resource, *, expected_version=None):
        raise NotImplementedError

    def delete(self, resource_type, resource_id, *, expected_version=None):
        raise NotImplementedError


def test_operation_does_nothing_by_default():
    """A storage that does not enclose its operations gets a context that does nothing."""
    with MinimalStorage().operation() as value:
        assert value is None


class MinimalAsyncStorage(AsyncScimStorage):
    async def get(self, resource_type, resource_id):
        raise NotImplementedError

    async def search(self, resource_types, search_request):
        raise NotImplementedError

    async def create(self, resource_type, resource):
        raise NotImplementedError

    async def update(self, resource_type, resource, *, expected_version=None):
        raise NotImplementedError

    async def delete(self, resource_type, resource_id, *, expected_version=None):
        raise NotImplementedError


def test_async_operation_does_nothing_by_default():
    """An asynchronous storage that does not enclose its operations gets a context that does nothing."""

    async def enter():
        async with MinimalAsyncStorage().operation() as value:
            return value

    assert asyncio.run(enter()) is None
