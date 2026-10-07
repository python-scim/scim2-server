import asyncio
import warnings

import pytest

from scim2_server.memory import AsyncInMemoryStorage
from scim2_server.memory import InMemoryStorage
from scim2_server.storage import AsyncScimStorage
from scim2_server.storage import ScimStorage


class MinimalStorage(ScimStorage):
    def get(self, resource_type, resource_id, *, response_parameters=None):
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
    async def get(self, resource_type, resource_id, *, response_parameters=None):
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
