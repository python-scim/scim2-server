import sys
from unittest.mock import patch

import httpx2
import pytest
import time_machine
from scim2_models import ExpiredCursorException
from scim2_models import InvalidCountException
from scim2_models import InvalidCursorException
from scim2_models import Pagination
from scim2_models import ScimProvider

from scim2_server.applications.wsgi import WSGIApplication
from scim2_server.cursor import Cursors
from scim2_server.handler import AsyncScimHandler
from scim2_server.memory import AsyncInMemoryStorage
from scim2_server.memory import InMemoryStorage
from scim2_server.service import ScimService
from scim2_server.storage import SearchPage

from .conftest import SECRET
from .conftest import BlockingHandler


def configured(scim_provider, **pagination):
    """Return the default provider, with a pagination configuration."""
    config = scim_provider.config.model_copy(
        update={"pagination": Pagination(**pagination)}, deep=True
    )
    return ScimProvider(
        models=scim_provider.models,
        resource_types=scim_provider.resource_types,
        config=config,
    )


@pytest.fixture(params=["sync", "async"])
def client_with(request, storage, scim_provider):
    """Build clients of applications paging with the given pagination configuration, served by the synchronous handler, then by the asynchronous one."""
    clients = []

    def build(**pagination):
        provider = configured(scim_provider, **pagination)
        application = WSGIApplication(storage, provider)
        if request.param == "async":
            application.handler = BlockingHandler(
                AsyncScimHandler(application.service, AsyncInMemoryStorage(storage))
            )
        client = httpx2.Client(
            transport=httpx2.WSGITransport(app=application),
            base_url="https://scim.example.com",
        )
        clients.append(client)
        for user_name in ("alice", "bob", "carol", "dave", "erin"):
            client.post("/v2/Users", json={"userName": user_name})
        return client

    yield build
    for client in clients:
        client.close()


@pytest.fixture
def client(client_with):
    return client_with(cursor=True, index=True)


def user_names(response):
    return [user["userName"] for user in response.json().get("Resources", [])]


def walk(client, **parameters):
    """Return every response of a search paged with a cursor, following nextCursor."""
    parameters = {"cursor": "", **parameters}
    responses = [client.get("/v2/Users", params=parameters)]
    while "nextCursor" in responses[-1].json():
        parameters["cursor"] = responses[-1].json()["nextCursor"]
        responses.append(client.get("/v2/Users", params=parameters))
    return responses


def test_next_cursors_page_every_resource(client):
    """Following nextCursor returns every resource once, with no startIndex."""
    responses = walk(client, count=2, sortBy="userName")

    assert [user_names(response) for response in responses] == [
        ["alice", "bob"],
        ["carol", "dave"],
        ["erin"],
    ]
    first = responses[0].json()
    assert first["totalResults"] == 5
    assert first["itemsPerPage"] == 2
    assert "startIndex" not in first
    assert "previousCursor" not in first


def test_previous_cursors_page_back_to_the_first_page(client):
    """Following previousCursor from the last page returns the pages before it, up to the first one."""
    parameters = {"count": 2, "sortBy": "userName"}
    pages = [walk(client, **parameters)[-1]]
    while "previousCursor" in pages[-1].json():
        cursor = pages[-1].json()["previousCursor"]
        pages.append(client.get("/v2/Users", params={"cursor": cursor, **parameters}))

    assert [user_names(page) for page in pages] == [
        ["erin"],
        ["carol", "dave"],
        ["alice", "bob"],
    ]
    assert "nextCursor" in pages[-1].json()


def test_an_empty_cursor_query_parameter_asks_for_the_first_page(client):
    """A cursor parameter without a value asks for cursor pagination."""
    response = client.get("/v2/Users?cursor&count=2&sortBy=userName")

    assert user_names(response) == ["alice", "bob"]
    assert "nextCursor" in response.json()


def test_cursors_are_url_safe(client):
    """A cursor only uses the unreserved characters of RFC 3986 §2.3, without padding."""
    cursor = client.get("/v2/Users", params={"cursor": "", "count": 2}).json()[
        "nextCursor"
    ]

    assert set(cursor) <= set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    )


def test_a_search_with_body_pages_with_a_cursor(client):
    """POST on .search takes a cursor too (RFC 9865 §3)."""
    body = {"count": 3, "sortBy": "userName"}
    first = client.post("/v2/Users/.search", json={"cursor": "", **body})
    second = client.post(
        "/v2/Users/.search",
        json={"cursor": first.json()["nextCursor"], **body},
    )

    assert user_names(first) + user_names(second) == [
        "alice",
        "bob",
        "carol",
        "dave",
        "erin",
    ]


def test_a_cursor_at_the_root(client):
    """A cursor pages a search at the root of the server."""
    client.post("/v2/Groups", json={"displayName": "admins"})

    first = client.get("/v2/", params={"cursor": "", "count": 4})
    second = client.get(
        "/v2/", params={"cursor": first.json()["nextCursor"], "count": 4}
    )

    resources = first.json()["Resources"] + second.json()["Resources"]
    assert len({resource["id"] for resource in resources}) == 6


def test_a_count_of_zero_returns_no_cursor(client):
    """With a count of 0, the response holds the total only, so the client does not loop."""
    response = client.get("/v2/Users", params={"cursor": "", "count": 0})

    assert response.json()["totalResults"] == 5
    assert "nextCursor" not in response.json()


def error(response):
    return response.status_code, response.json().get("scimType")


def test_a_cursor_and_a_start_index_are_refused(client):
    """A search cannot ask for both pagination methods."""
    response = client.get("/v2/Users", params={"cursor": "", "startIndex": 1})

    assert error(response) == (400, "invalidValue")


def test_a_null_cursor_with_a_start_index_pages_by_index(client):
    """A cursor without value does not ask for cursor pagination."""
    response = client.post(
        "/v2/Users/.search",
        json={"cursor": None, "startIndex": 2, "count": 2, "sortBy": "userName"},
    )

    assert response.json()["startIndex"] == 2
    assert user_names(response) == ["bob", "carol"]


def test_a_forged_cursor_is_refused(client):
    """A cursor the server did not issue is invalid."""
    cursor = client.get("/v2/Users", params={"cursor": "", "count": 2}).json()[
        "nextCursor"
    ]
    forged = cursor[:-2] + ("AA" if cursor[-2:] != "AA" else "BB")

    response = client.get("/v2/Users", params={"cursor": forged, "count": 2})

    assert error(response) == (400, "invalidCursor")


@pytest.mark.parametrize("cursor", ["garbage", "A", "AQ", "x.y~z"])
def test_a_malformed_cursor_is_refused(client, cursor):
    """A cursor that does not decode is invalid, and does not break the server."""
    response = client.get("/v2/Users", params={"cursor": cursor, "count": 2})

    assert error(response) == (400, "invalidCursor")


@pytest.mark.parametrize(
    "changed",
    [
        {"sortBy": "displayName"},
        {"filter": 'userName ne "bob"'},
        {"attributes": "displayName"},
    ],
)
def test_a_cursor_of_another_query_is_refused(client, changed):
    """The next pages must keep the parameters of the first one (RFC 9865 §2)."""
    parameters = {"count": 2, "sortBy": "userName"}
    cursor = client.get("/v2/Users", params={"cursor": "", **parameters}).json()[
        "nextCursor"
    ]

    response = client.get(
        "/v2/Users", params={"cursor": cursor, **parameters, **changed}
    )

    assert error(response) == (400, "invalidCursor")


def test_a_cursor_of_another_endpoint_is_refused(client):
    """A cursor issued for the users does not page the groups."""
    cursor = client.get("/v2/Users", params={"cursor": "", "count": 2}).json()[
        "nextCursor"
    ]

    response = client.get("/v2/Groups", params={"cursor": cursor, "count": 2})

    assert error(response) == (400, "invalidCursor")


def test_a_cursor_with_another_count_is_refused(client):
    """The count of the next pages must be the count of the first one (RFC 9865 §2.1)."""
    cursor = client.get("/v2/Users", params={"cursor": "", "count": 2}).json()[
        "nextCursor"
    ]

    response = client.get("/v2/Users", params={"cursor": cursor, "count": 3})

    assert error(response) == (400, "invalidCount")


def test_a_cursor_of_another_secret_is_refused(client):
    """A server only opens the cursors sealed with its own secret."""
    sealed = Cursors("another secret").seal("query", 2, 2)

    response = client.get("/v2/Users", params={"cursor": sealed, "count": 2})

    assert error(response) == (400, "invalidCursor")


def test_an_old_cursor_expires_after_the_cursor_timeout(client_with):
    """A cursor older than cursorTimeout is refused."""
    client = client_with(cursor=True, index=True, cursor_timeout=60)
    with time_machine.travel(0, tick=False):
        cursor = client.get("/v2/Users", params={"cursor": "", "count": 2}).json()[
            "nextCursor"
        ]
    with time_machine.travel(59, tick=False):
        assert (
            client.get("/v2/Users", params={"cursor": cursor, "count": 2}).status_code
            == 200
        )
    with time_machine.travel(61, tick=False):
        response = client.get("/v2/Users", params={"cursor": cursor, "count": 2})

    assert error(response) == (400, "expiredCursor")


def test_without_pagination_configuration_the_cursors_are_not_supported(wsgi):
    """A server that does not announce cursor pagination refuses cursors with a 501."""
    response = wsgi.get("/v2/Users", params={"cursor": ""})

    assert response.status_code == 501


def test_an_index_the_configuration_does_not_announce_is_refused(client_with):
    """A server that only announces cursors refuses startIndex with a 501."""
    client = client_with(cursor=True, index=False)

    response = client.get("/v2/Users", params={"startIndex": 1})

    assert response.status_code == 501


@pytest.mark.parametrize(
    ("pagination", "cursor"),
    [
        ({"cursor": True, "index": True}, False),
        ({"cursor": True, "index": True, "default_pagination_method": "cursor"}, True),
        ({"cursor": True, "index": True, "default_pagination_method": "index"}, False),
        ({"cursor": True, "index": False}, True),
    ],
)
def test_the_default_pagination_method(client_with, pagination, cursor):
    """A search without pagination parameter follows defaultPaginationMethod, or else the index (RFC 9865 §2.4)."""
    client = client_with(**pagination)

    response = client.get("/v2/Users", params={"count": 2})

    assert ("nextCursor" in response.json()) is cursor
    assert ("startIndex" in response.json()) is not cursor


def test_a_search_without_count_gets_the_default_page_size(client_with):
    """Without count, a page holds defaultPageSize resources."""
    client = client_with(cursor=True, index=True, default_page_size=2)

    response = client.get("/v2/Users", params={"cursor": "", "sortBy": "userName"})

    assert user_names(response) == ["alice", "bob"]


@pytest.mark.parametrize(
    ("max_results", "pagination", "expected"),
    [
        (1000, {"max_page_size": 3}, 3),
        (2, {"max_page_size": 3}, 2),
        (1000, {"max_page_size": 3, "default_page_size": 4}, 3),
    ],
)
def test_the_count_is_bounded_by_the_smallest_limit(
    storage, scim_provider, max_results, pagination, expected
):
    """A page never holds more than maxResults nor maxPageSize resources, whichever is smaller."""
    provider = configured(scim_provider, cursor=False, index=True, **pagination)
    provider.config.filter.max_results = max_results
    application = WSGIApplication(storage, provider)
    with httpx2.Client(
        transport=httpx2.WSGITransport(app=application),
        base_url="https://scim.example.com",
    ) as client:
        for user_name in ("alice", "bob", "carol", "dave", "erin"):
            client.post("/v2/Users", json={"userName": user_name})

        unbounded = client.get("/v2/Users")
        bounded = client.get("/v2/Users", params={"count": 10})

    assert len(unbounded.json()["Resources"]) == expected
    assert len(bounded.json()["Resources"]) == expected


def test_a_deleted_resource_does_not_shift_the_next_page(client):
    """Deleting the last resource of a page does not skip the first resource of the next page."""
    parameters = {"count": 2, "sortBy": "userName"}
    first = client.get("/v2/Users", params={"cursor": "", **parameters})
    client.delete(f"/v2/Users/{first.json()['Resources'][-1]['id']}")

    second = client.get(
        "/v2/Users", params={"cursor": first.json()["nextCursor"], **parameters}
    )

    assert user_names(second) == ["carol", "dave"]


def test_a_storage_without_cursors_is_refused_when_they_are_announced(scim_provider):
    """A server whose configuration announces cursors does not start with a storage that does not support them."""

    class IndexStorage(InMemoryStorage):
        supports_cursors = False

    provider = configured(scim_provider, cursor=True, index=True)

    with pytest.raises(TypeError, match="IndexStorage"):
        WSGIApplication(IndexStorage(), provider)


def test_a_storage_without_positions_is_refused_when_cursors_are_announced(
    scim_provider,
):
    """A storage whose search method predates the positions cannot page with cursors."""
    with pytest.warns(DeprecationWarning):

        class OldStorage(InMemoryStorage):
            def search(self, resource_types, search_request):
                raise NotImplementedError

    provider = configured(scim_provider, cursor=True, index=True)

    with pytest.raises(TypeError, match="OldStorage"):
        WSGIApplication(OldStorage(), provider)


def test_an_async_storage_without_cursors_is_refused_when_they_are_announced(
    scim_provider,
):
    """The asynchronous handler refuses a storage without cursors too."""

    class IndexStorage(AsyncInMemoryStorage):
        supports_cursors = False

    provider = configured(scim_provider, cursor=True, index=True)
    service = ScimService(provider, secret=SECRET)

    with pytest.raises(TypeError, match="IndexStorage"):
        AsyncScimHandler(service, IndexStorage())


def test_a_storage_without_cursors_serves_a_server_that_does_not_announce_them(
    scim_provider,
):
    """A storage without cursors serves a server that pages by index only."""

    class IndexStorage(InMemoryStorage):
        supports_cursors = False

    application = WSGIApplication(IndexStorage(), scim_provider)

    with httpx2.Client(
        transport=httpx2.WSGITransport(app=application),
        base_url="https://scim.example.com",
    ) as client:
        assert client.get("/v2/Users").status_code == 200


def test_cursors_need_a_secret(scim_provider):
    """A service that announces cursor pagination cannot work without a secret."""
    provider = configured(scim_provider, cursor=True, index=True)

    with pytest.raises(ValueError, match="secret"):
        ScimService(provider)


def test_cursors_need_the_cursor_extra(scim_provider, monkeypatch):
    """A service that announces cursor pagination tells which extra it needs."""
    monkeypatch.setitem(sys.modules, "scim2_server.cursor", None)
    provider = configured(scim_provider, cursor=True, index=True)

    with pytest.raises(ModuleNotFoundError, match=r"scim2-server\[cursor\]"):
        ScimService(provider, secret=SECRET)


def test_a_service_without_cursors_needs_no_secret(scim_provider, storage):
    """A service that only pages by index has no cursor to seal, and needs no secret."""
    application = WSGIApplication(storage, scim_provider, ScimService(scim_provider))

    with httpx2.Client(
        transport=httpx2.WSGITransport(app=application),
        base_url="https://scim.example.com",
    ) as client:
        assert client.get("/v2/Users").status_code == 200


def test_a_cursor_opens_back_to_its_position():
    """A sealed position comes back intact, whatever its JSON shape."""
    cursors = Cursors(b"bytes work too")
    position = {"d": "next", "k": "bob", "i": "7"}

    assert cursors.open(cursors.seal("query", 2, position), "query", 2, None) == (
        position
    )


@pytest.mark.parametrize(
    ("query", "count", "exception"),
    [
        ("other", 2, InvalidCursorException),
        ("query", 3, InvalidCountException),
    ],
)
def test_a_cursor_only_opens_for_its_query(query, count, exception):
    """A cursor refuses a query or a count it was not issued for."""
    cursors = Cursors(SECRET)

    with pytest.raises(exception):
        cursors.open(cursors.seal("query", 2, 0), query, count, None)


def test_a_cursor_expires():
    """A cursor older than the timeout is refused."""
    cursors = Cursors(SECRET)
    with time_machine.travel(0, tick=False):
        cursor = cursors.seal("query", 2, 0)

    with time_machine.travel(100, tick=False), pytest.raises(ExpiredCursorException):
        cursors.open(cursor, "query", 2, 60)


def test_a_storage_may_leave_out_the_total_of_a_cursor_page(client, storage):
    """With a cursor, a storage that cannot count the resources leaves totalResults out (RFC 9865 §2)."""
    with patch.object(storage, "search", return_value=SearchPage(None, [])):
        response = client.get("/v2/Users", params={"cursor": "", "count": 2})

    assert response.status_code == 200
    assert "totalResults" not in response.json()


def test_a_page_by_index_needs_a_total(client, storage):
    """By index, a storage that gives no total breaks the contract, and the server answers 500."""
    with patch.object(storage, "search", return_value=SearchPage(None, [])):
        response = client.get("/v2/Users", params={"startIndex": 1})

    assert response.status_code == 500


def test_an_application_reads_the_secret_of_its_service_from_the_environment(
    storage, scim_provider
):
    """The default service of an application seals cursors with SCIM2_SERVER_SECRET."""
    provider = configured(scim_provider, cursor=True, index=True)
    application = WSGIApplication(storage, provider)

    with httpx2.Client(
        transport=httpx2.WSGITransport(app=application),
        base_url="https://scim.example.com",
    ) as client:
        response = client.get("/v2/Users", params={"cursor": "", "count": 2})

    assert response.status_code == 200


def test_an_application_without_secret_refuses_the_cursors(
    storage, scim_provider, monkeypatch
):
    """Without SCIM2_SERVER_SECRET, an application that announces cursors cannot start."""
    monkeypatch.delenv("SCIM2_SERVER_SECRET")
    provider = configured(scim_provider, cursor=True, index=True)

    with pytest.raises(ValueError, match="secret"):
        WSGIApplication(storage, provider)
