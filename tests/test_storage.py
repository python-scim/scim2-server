import asyncio
import datetime
import json
import warnings

import pytest
from scim2_models import InvalidCursorException
from scim2_models import SearchRequest

from scim2_server.memory import AsyncInMemoryStorage
from scim2_server.memory import InMemoryStorage
from scim2_server.storage import AsyncScimStorage
from scim2_server.storage import ScimStorage
from scim2_server.storage import async_search_page
from scim2_server.storage import search_page


class MinimalStorage(ScimStorage):
    def get(self, resource_type, resource_id, *, response_parameters=None):
        raise NotImplementedError

    def search(self, resource_types, search_request, *, position=None):
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
    async def get(self, resource_type, resource_id, *, response_parameters=None):
        raise NotImplementedError

    async def search(self, resource_types, search_request, *, position=None):
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


class RecordingStorage(InMemoryStorage):
    def __init__(self):
        super().__init__()
        self.response_parameters = []

    def get(self, resource_type, resource_id, *, response_parameters=None):
        self.response_parameters.append(response_parameters)
        return super().get(resource_type, resource_id)


def test_a_get_without_response_parameters_is_deprecated():
    """A storage whose get method does not accept response_parameters warns where it is defined."""
    with pytest.warns(DeprecationWarning, match="scim2-server 0.9") as record:

        class OldStorage(InMemoryStorage):
            def get(self, resource_type, resource_id):
                raise NotImplementedError

    assert record[0].filename == __file__
    assert not OldStorage._get_takes_response_parameters


def test_an_async_get_without_response_parameters_is_deprecated():
    """An asynchronous storage whose get method does not accept response_parameters warns too."""
    with pytest.warns(DeprecationWarning, match="scim2-server 0.9") as record:

        class OldStorage(AsyncInMemoryStorage):
            async def get(self, resource_type, resource_id):
                raise NotImplementedError

    assert record[0].filename == __file__
    assert not OldStorage._get_takes_response_parameters


def test_a_get_with_keyword_arguments_takes_response_parameters():
    """A get method that accepts any keyword argument is not deprecated."""
    with warnings.catch_warnings():
        warnings.simplefilter("error")

        class KeywordStorage(InMemoryStorage):
            def get(self, resource_type, resource_id, **kwargs):
                raise NotImplementedError

    assert KeywordStorage._get_takes_response_parameters


class TestResponseParameters:
    @pytest.fixture
    def storage(self):
        return RecordingStorage()

    def test_a_read_passes_the_response_parameters(self, wsgi, storage):
        """The storage receives the response parameters of a GET request."""
        user = wsgi.post("/v2/Users", json={"userName": "bjensen"}).json()

        response = wsgi.get(f"/v2/Users/{user['id']}?excludedAttributes=emails")

        assert response.status_code == 200
        assert [
            str(path) for path in storage.response_parameters[-1].excluded_attributes
        ] == ["emails"]

    def test_a_replacement_reads_the_whole_resource(self, wsgi, storage):
        """The storage receives no response parameters when the server needs the whole resource."""
        user = wsgi.post("/v2/Users", json={"userName": "bjensen"}).json()

        wsgi.put(
            f"/v2/Users/{user['id']}?excludedAttributes=emails",
            json={"userName": "bjensen", "displayName": "Babs"},
        )

        assert storage.response_parameters == [None]


class TestDeprecatedStorage:
    @pytest.fixture
    def storage(self):
        with pytest.warns(DeprecationWarning):

            class DeprecatedStorage(InMemoryStorage):
                def get(self, resource_type, resource_id):
                    return super().get(resource_type, resource_id)

        return DeprecatedStorage()

    def test_a_deprecated_storage_still_serves_reads(self, wsgi):
        """A storage whose get method does not accept response_parameters still answers a GET request."""
        user = wsgi.post("/v2/Users", json={"userName": "bjensen"}).json()

        response = wsgi.get(f"/v2/Users/{user['id']}?excludedAttributes=emails")

        assert response.status_code == 200
        assert response.json()["userName"] == "bjensen"


def users(storage, user_type, scim_provider, *names):
    User = scim_provider.model_for(user_type)
    return [storage.create(user_type, User(user_name=name)) for name in names]


def test_a_search_without_position_is_deprecated():
    """A storage whose search method does not accept position warns where it is defined."""
    with pytest.warns(DeprecationWarning, match="position") as record:

        class OldStorage(InMemoryStorage):
            def search(self, resource_types, search_request):
                raise NotImplementedError

    assert record[0].filename == __file__
    assert not OldStorage._search_takes_position


def test_an_async_search_without_position_is_deprecated():
    """An asynchronous storage whose search method does not accept position warns too."""
    with pytest.warns(DeprecationWarning, match="position"):

        class OldStorage(AsyncInMemoryStorage):
            async def search(self, resource_types, search_request):
                raise NotImplementedError

    assert not OldStorage._search_takes_position


def test_a_deprecated_search_pages_by_index(user_type, scim_provider):
    """The server reads the number of resources and the page that a search without position returns."""
    with pytest.warns(DeprecationWarning):

        class OldStorage(InMemoryStorage):
            def search(self, resource_types, search_request):
                page = super().search(resource_types, search_request)
                return page.total, page.resources

    storage = OldStorage()
    created = users(storage, user_type, scim_provider, "alice", "bob", "carol")

    page = search_page(
        storage, [user_type], SearchRequest(start_index=2, count=1), None
    )

    assert page.total == 3
    assert page.resources[0].id == sorted(user.id for user in created)[1]
    assert (page.next, page.previous) == (None, None)


def test_a_deprecated_async_search_pages_by_index(user_type, scim_provider):
    """An asynchronous search without position is read the same way."""
    with pytest.warns(DeprecationWarning):

        class OldStorage(AsyncInMemoryStorage):
            async def search(self, resource_types, search_request):
                page = await super().search(resource_types, search_request)
                return page.total, page.resources

    storage = OldStorage()
    users(storage.storage, user_type, scim_provider, "alice", "bob", "carol")

    page = asyncio.run(
        async_search_page(storage, [user_type], SearchRequest(count=2), None)
    )

    assert page.total == 3
    assert len(page.resources) == 2


def test_a_search_with_keyword_arguments_returning_a_tuple_pages_by_index(
    user_type, scim_provider
):
    """A storage that forwards any keyword argument to an older search still pages by index."""

    class ForwardingStorage(InMemoryStorage):
        def search(self, resource_types, search_request, **kwargs):
            page = super().search(resource_types, search_request)
            return page.total, page.resources

    storage = ForwardingStorage()
    users(storage, user_type, scim_provider, "alice", "bob", "carol")

    page = search_page(storage, [user_type], SearchRequest(start_index=3), None)

    assert page.total == 3
    assert len(page.resources) == 1


def test_an_async_search_with_keyword_arguments_returning_a_tuple_pages_by_index(
    user_type, scim_provider
):
    """An asynchronous storage that forwards any keyword argument to an older search still pages by index."""

    class ForwardingStorage(AsyncInMemoryStorage):
        async def search(self, resource_types, search_request, **kwargs):
            page = await super().search(resource_types, search_request)
            return page.total, page.resources

    storage = ForwardingStorage()
    users(storage.storage, user_type, scim_provider, "alice", "bob", "carol")

    page = asyncio.run(
        async_search_page(storage, [user_type], SearchRequest(start_index=3), None)
    )

    assert page.total == 3
    assert len(page.resources) == 1


@pytest.mark.parametrize(
    "position",
    [
        2,
        "2",
        [1],
        {"d": "next", "k": None},
        {"d": "sideways", "k": None, "i": "1"},
        {"d": "next", "k": None, "i": 1},
        {"d": "next", "k": {"datetime": "yesterday"}, "i": "1"},
        {"d": "next", "k": {"date": "2026-10-09"}, "i": "1"},
    ],
)
def test_a_position_that_does_not_come_from_a_page_is_refused(user_type, position):
    """A position that the storage did not give raises invalidCursor."""
    with pytest.raises(InvalidCursorException):
        InMemoryStorage().search(
            [user_type], SearchRequest(cursor="", count=2), position=position
        )


def test_the_resources_are_sorted_by_identifier_without_sort_by(
    user_type, scim_provider
):
    """Without sortBy, the resources come in the order of their identifiers, by index as by cursor."""
    storage = InMemoryStorage()
    created = users(storage, user_type, scim_provider, "alice", "bob", "carol")
    expected = sorted(user.id for user in created)

    by_index = storage.search([user_type], SearchRequest())
    by_cursor = storage.search([user_type], SearchRequest(cursor=""))

    assert [user.id for user in by_index.resources] == expected
    assert [user.id for user in by_cursor.resources] == expected


def test_a_cursor_pages_by_date(user_type, scim_provider):
    """The positions of a search sorted by a date go through JSON."""
    dates = iter(
        datetime.datetime(2026, 10, day, tzinfo=datetime.UTC) for day in (3, 1, 2)
    )
    storage = InMemoryStorage(clock=lambda: next(dates))
    users(storage, user_type, scim_provider, "carol", "alice", "bob")
    search_request = SearchRequest(cursor="", count=1, sort_by="meta.created")

    pages = [storage.search([user_type], search_request)]
    while pages[-1].next is not None:
        position = json.loads(json.dumps(pages[-1].next))
        pages.append(storage.search([user_type], search_request, position=position))

    assert [page.resources[0].user_name for page in pages] == ["alice", "bob", "carol"]


def test_a_cursor_refuses_a_sort_value_it_cannot_hold(
    user_type, scim_provider, monkeypatch
):
    """A sort value of a type that JSON cannot hold, from a custom model, is refused."""
    storage = InMemoryStorage()
    users(storage, user_type, scim_provider, "alice", "bob")
    value = object()
    monkeypatch.setattr(SearchRequest, "sort_value", lambda self, resource: value)

    with pytest.raises(TypeError, match="cannot hold"):
        storage.search(
            [user_type], SearchRequest(cursor="", count=1, sort_by="userName")
        )
