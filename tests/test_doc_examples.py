import asyncio
import sqlite3

import httpx2
import pytest
from scim2_models import ResourceType
from scim2_models import ScimProvider
from scim2_models import User

from doc._examples import asgi_integration
from doc._examples import wsgi_integration
from doc._examples.async_cursor_storage import AsyncCursorSQLiteStorage
from doc._examples.async_library_storage import AsyncLibraryStorage
from doc._examples.async_sqlite_storage import AsyncSQLiteStorage
from doc._examples.contract_without_root_search import TestStorageWithoutRootSearch
from doc._examples.contract_without_search_features import (
    TestStorageWithoutFilterNorSort,
)
from doc._examples.cursor_storage import CursorSQLiteStorage
from doc._examples.library_storage import BOOKS
from doc._examples.library_storage import MEMBERS
from doc._examples.library_storage import Book
from doc._examples.library_storage import LibraryMember
from doc._examples.library_storage import LibraryStorage
from doc._examples.sqlite_storage import SQLiteStorage
from scim2_server.applications.asgi import ASGIApplication
from scim2_server.applications.wsgi import WSGIApplication
from scim2_server.testing import AsyncScimStorageContract
from scim2_server.testing import ScimStorageContract
from scim2_server.utils import load_default_provider
from scim2_server.utils import load_default_service_provider_config

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


def provider_without_filter_nor_sort():
    provider = load_default_provider()
    provider.config.filter.supported = False
    provider.config.sort.supported = False
    return provider


class TestCursorSQLiteStorage(ScimStorageContract):
    """The cursor storage of the "Page the results with cursors" guide follows the storage contract."""

    @pytest.fixture
    def provider(self):
        return provider_without_filter_nor_sort()

    @pytest.fixture
    def storage(self, provider):
        connection = sqlite3.connect(":memory:")
        yield CursorSQLiteStorage(connection, provider)
        connection.close()


class TestAsyncCursorSQLiteStorage(AsyncScimStorageContract):
    """The asynchronous cursor storage of the "Page the results with cursors" guide follows the storage contract."""

    @pytest.fixture
    def provider(self):
        return provider_without_filter_nor_sort()

    @pytest.fixture
    def async_storage(self, provider):
        storage = AsyncCursorSQLiteStorage(":memory:", provider)
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


EXISTING_MEMBER = (
    "INSERT INTO members (login, email, first_name, last_name, card_number, active,"
    " created_at, updated_at) VALUES ('bjensen', 'bjensen@example.com', 'Barbara',"
    " 'Jensen', '00000042', 1, '2024-01-01T00:00:00+00:00',"
    " '2024-01-01T00:00:00+00:00')"
)
MEMBER_EXTENSION = str(LibraryMember.__schema__)
NEW_MEMBER = {
    "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
    "userName": "jsmith",
    "name": {"givenName": "John", "familyName": "Smith"},
    "emails": [{"value": "jsmith@example.com", "primary": True}],
    MEMBER_EXTENSION: {"cardNumber": "chosen by the client"},
}

NEW_BOOK = {
    "schemas": [str(Book.__schema__)],
    "title": "Dune",
    "isbn": "978-0441013593",
}


@pytest.fixture
def library_provider():
    return ScimProvider(
        models=[User, LibraryMember, Book],
        resource_types=[
            ResourceType.from_resource(User[LibraryMember]),
            ResourceType.from_resource(Book),
        ],
        config=load_default_service_provider_config(),
    )


def library_scenario(client):
    """Send the requests of a SCIM client to the members and the books of the library."""
    responses = {"existing": client.get("/v2/Users/1")}
    created = responses["created"] = client.post("/v2/Users", json=NEW_MEMBER)
    location = f"/v2/Users/{created.json()['id']}"
    replacement = {**NEW_MEMBER, "name": {"givenName": "Johnny", "familyName": "Smith"}}
    responses["outdated"] = client.put(
        location, json=replacement, headers={"If-Match": 'W/"outdated"'}
    )
    responses["replaced"] = client.put(
        location,
        json=replacement,
        headers={"If-Match": created.headers["ETag"]},
    )
    responses["duplicate"] = client.post(
        "/v2/Users", json={**NEW_MEMBER, "userName": "BJENSEN"}
    )
    responses["found"] = client.get(
        "/v2/Users", params={"filter": 'userName eq "jsmith"'}
    )
    responses["deleted"] = client.delete(location)
    responses["gone"] = client.get(location)
    responses["unknown"] = client.get("/v2/Users/unknown")
    book = responses["book"] = client.post("/v2/Books", json=NEW_BOOK)
    responses["renamed_book"] = client.put(
        f"/v2/Books/{book.json()['id']}", json={**NEW_BOOK, "title": "Dune Messiah"}
    )
    responses["same_isbn"] = client.post("/v2/Books", json=NEW_BOOK)
    responses["everything"] = client.post(
        "/v2/.search",
        json={"schemas": ["urn:ietf:params:scim:api:messages:2.0:SearchRequest"]},
    )
    return responses


async def async_library_scenario(client):
    """Send the requests of a SCIM client to the members and the books of the library, asynchronously."""
    responses = {"existing": await client.get("/v2/Users/1")}
    created = responses["created"] = await client.post("/v2/Users", json=NEW_MEMBER)
    location = f"/v2/Users/{created.json()['id']}"
    replacement = {**NEW_MEMBER, "name": {"givenName": "Johnny", "familyName": "Smith"}}
    responses["outdated"] = await client.put(
        location, json=replacement, headers={"If-Match": 'W/"outdated"'}
    )
    responses["replaced"] = await client.put(
        location,
        json=replacement,
        headers={"If-Match": created.headers["ETag"]},
    )
    responses["duplicate"] = await client.post(
        "/v2/Users", json={**NEW_MEMBER, "userName": "BJENSEN"}
    )
    responses["found"] = await client.get(
        "/v2/Users", params={"filter": 'userName eq "jsmith"'}
    )
    responses["deleted"] = await client.delete(location)
    responses["gone"] = await client.get(location)
    responses["unknown"] = await client.get("/v2/Users/unknown")
    book = responses["book"] = await client.post("/v2/Books", json=NEW_BOOK)
    responses["renamed_book"] = await client.put(
        f"/v2/Books/{book.json()['id']}", json={**NEW_BOOK, "title": "Dune Messiah"}
    )
    responses["same_isbn"] = await client.post("/v2/Books", json=NEW_BOOK)
    responses["everything"] = await client.post(
        "/v2/.search",
        json={"schemas": ["urn:ietf:params:scim:api:messages:2.0:SearchRequest"]},
    )
    return responses


def check_library_responses(responses):
    existing = responses["existing"].json()
    assert existing["userName"] == "bjensen"
    assert existing["name"] == {"givenName": "Barbara", "familyName": "Jensen"}
    assert existing["emails"] == [{"value": "bjensen@example.com", "primary": True}]
    assert existing[MEMBER_EXTENSION] == {"cardNumber": "00000042"}
    assert responses["existing"].headers["ETag"] == 'W/"2024-01-01T00:00:00+00:00"'

    created = responses["created"]
    card_number = created.json()[MEMBER_EXTENSION]["cardNumber"]
    assert created.status_code == 201
    assert card_number != "chosen by the client"
    assert len(card_number) == 8

    assert responses["outdated"].status_code == 412
    replaced = responses["replaced"].json()
    assert replaced["name"]["givenName"] == "Johnny"
    assert replaced[MEMBER_EXTENSION] == {"cardNumber": card_number}
    assert responses["duplicate"].status_code == 409
    assert responses["found"].json()["totalResults"] == 1
    assert responses["deleted"].status_code == 204
    assert responses["gone"].status_code == 404
    assert responses["unknown"].status_code == 404
    assert responses["book"].status_code == 201
    assert responses["book"].json()["title"] == "Dune"
    assert responses["renamed_book"].json()["title"] == "Dune Messiah"
    assert responses["renamed_book"].json()["isbn"] == "978-0441013593"
    assert responses["same_isbn"].status_code == 409
    assert responses["everything"].json()["totalResults"] == 2


def test_library_storage(library_provider):
    """The storage of the "Serve an existing data model" guide serves the rows of the members and books tables."""
    connection = sqlite3.connect(":memory:")
    connection.execute(MEMBERS)
    connection.execute(BOOKS)
    connection.execute(EXISTING_MEMBER)
    app = WSGIApplication(LibraryStorage(connection), library_provider)
    transport = httpx2.WSGITransport(app=app)
    with httpx2.Client(transport=transport, base_url="https://scim.example") as client:
        responses = library_scenario(client)
    connection.close()

    check_library_responses(responses)


def test_async_library_storage(library_provider):
    """The asynchronous storage of the "Serve an existing data model" guide serves the rows of the members and books tables."""

    async def scenario():
        storage = AsyncLibraryStorage(":memory:")
        connection = await storage.connect()
        await connection.execute(MEMBERS)
        await connection.execute(BOOKS)
        await connection.execute(EXISTING_MEMBER)
        await connection.commit()
        app = ASGIApplication(storage, library_provider)
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(
            transport=transport, base_url="https://scim.example"
        ) as client:
            responses = await async_library_scenario(client)
        await storage.close()
        return responses

    check_library_responses(asyncio.run(scenario()))
