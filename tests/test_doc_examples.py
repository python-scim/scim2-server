import asyncio
import sqlite3

import httpx2
import pytest

from doc._examples import asgi_integration
from doc._examples import wsgi_integration
from doc._examples.async_sqlite_storage import AsyncSQLiteStorage
from doc._examples.contract_without_root_search import TestStorageWithoutRootSearch
from doc._examples.contract_without_search_features import (
    TestStorageWithoutFilterNorSort,
)
from doc._examples.sqlite_storage import SQLiteStorage
from scim2_server.testing import AsyncScimStorageContract
from scim2_server.testing import ScimStorageContract

__all__ = ["TestStorageWithoutFilterNorSort", "TestStorageWithoutRootSearch"]


class TestSQLiteStorage(ScimStorageContract):
    """The storage of the "Write a storage" guide follows the storage contract."""

    @pytest.fixture
    def storage(self, provider):
        connection = sqlite3.connect(":memory:")
        yield SQLiteStorage(connection, provider)
        connection.close()


class TestAsyncSQLiteStorage(AsyncScimStorageContract):
    """The asynchronous storage of the "Write a storage" guide follows the storage contract."""

    @pytest.fixture
    def async_storage(self, provider):
        storage = AsyncSQLiteStorage(":memory:", provider)
        yield storage
        asyncio.run(storage.close())


USER = {
    "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
    "userName": "bjensen",
}


def test_wsgi_integration():
    """The WSGI integration of the "Integrate a web framework" guide creates and reads a resource."""
    transport = httpx2.WSGITransport(app=wsgi_integration.application)
    with httpx2.Client(transport=transport, base_url="https://scim.example") as client:
        created = client.post("/Users", json=USER)
        read = client.get(f"/Users/{created.json()['id']}")
        missing = client.get("/Users/unknown")
        unrouted = client.delete("/Users")

    assert created.status_code == 201
    assert created.headers["Location"] == read.json()["meta"]["location"]
    assert read.json()["userName"] == "bjensen"
    assert missing.status_code == 404
    assert unrouted.status_code == 405
    assert unrouted.headers["Allow"] == "GET, POST"


def test_asgi_integration():
    """The ASGI integration of the "Integrate a web framework" guide creates and reads a resource."""

    async def scenario():
        transport = httpx2.ASGITransport(app=asgi_integration.application)
        async with httpx2.AsyncClient(
            transport=transport, base_url="https://scim.example"
        ) as client:
            created = await client.post("/Users", json=USER)
            read = await client.get(f"/Users/{created.json()['id']}")
            missing = await client.get("/Users/unknown")
            unrouted = await client.delete("/Users")
        return created, read, missing, unrouted

    created, read, missing, unrouted = asyncio.run(scenario())

    assert created.status_code == 201
    assert created.headers["Location"] == read.json()["meta"]["location"]
    assert read.json()["userName"] == "bjensen"
    assert missing.status_code == 404
    assert unrouted.status_code == 405
    assert unrouted.headers["Allow"] == "GET, POST"
