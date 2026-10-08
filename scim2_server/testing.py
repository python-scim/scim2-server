"""A test suite checking that a storage follows the :class:`~scim2_server.storage.ScimStorage` contract.

Subclass :class:`ScimStorageContract` in the tests of a storage, and give it a
``storage`` fixture returning a new, empty storage::

    from scim2_server.testing import ScimStorageContract


    class TestMyStorage(ScimStorageContract):
        @pytest.fixture
        def storage(self):
            return MyStorage()

For an :class:`~scim2_server.storage.AsyncScimStorage`, subclass
:class:`AsyncScimStorageContract` and give it an ``async_storage`` fixture
instead.

The suite needs the ``testing`` extra, which installs pytest.
"""

import asyncio
from collections.abc import Coroutine
from collections.abc import Generator
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from typing import TypeVar
from typing import Union
from typing import cast

import pytest
from scim2_models import Context
from scim2_models import NotFoundException
from scim2_models import PreconditionFailedException
from scim2_models import Resource
from scim2_models import ResourceType
from scim2_models import ResponseParameters
from scim2_models import ScimProvider
from scim2_models import SearchRequest
from scim2_models import UniquenessException

from scim2_server.storage import AsyncScimStorage
from scim2_server.storage import ScimStorage
from scim2_server.storage import SearchPage
from scim2_server.storage import async_search_page
from scim2_server.storage import projection
from scim2_server.storage import search_page
from scim2_server.utils import load_default_provider
from scim2_server.utils import parametrize

T = TypeVar("T")


class ScimStorageContract:
    """The rules every :class:`~scim2_server.storage.ScimStorage` follows.

    The suite uses the ``User`` and ``Group`` resource types of the
    ``provider`` fixture, which serves the default resource types of
    :rfc:`RFC 7643 <7643>` unless overridden. The tests of a feature that the
    configuration of the provider does not announce, such as sorting, are
    skipped. So are the tests of the cursors, unless the storage
    :attr:`~scim2_server.storage.ScimStorage.supports_cursors`.
    """

    supports_root_search: bool = True
    """Whether the storage searches several resource types at once.

    Set it to :data:`False` for a storage raising
    :class:`~scim2_models.NotImplementedException` in that case.
    """

    @pytest.fixture
    def provider(self) -> ScimProvider:
        """Return the description of the service the storage serves."""
        return load_default_provider()

    @pytest.fixture
    def user_type(self, provider: ScimProvider) -> ResourceType:
        return next(rt for rt in provider.resource_types if rt.id == "User")

    @pytest.fixture
    def group_type(self, provider: ScimProvider) -> ResourceType:
        return next(rt for rt in provider.resource_types if rt.id == "Group")

    @pytest.fixture
    def user_model(
        self, provider: ScimProvider, user_type: ResourceType
    ) -> type[Resource[Any]]:
        return cast(type[Resource[Any]], provider.model_for(user_type))

    @pytest.fixture
    def group_model(
        self, provider: ScimProvider, group_type: ResourceType
    ) -> type[Resource[Any]]:
        return cast(type[Resource[Any]], provider.model_for(group_type))

    @staticmethod
    def search_request(
        models: list[type[Resource[Any]]], **parameters: Any
    ) -> SearchRequest[Any]:
        """Build a search request on the given models, as the server builds it."""
        return parametrize(SearchRequest, Union[tuple(models)])(**parameters)  # noqa: UP007

    @staticmethod
    def search(
        storage: Any,
        resource_types: list[ResourceType],
        search_request: SearchRequest[Any],
        position: Any = None,
    ) -> Any:
        """Search a storage as the server does, whether its search method accepts a position or not."""
        return search_page(storage, resource_types, search_request, position)

    @staticmethod
    def require(supported: bool, feature: str) -> None:
        """Skip the current test when the service does not support a feature."""
        if not supported:
            pytest.skip(f"The service does not support {feature}")

    @staticmethod
    def announces(provider: ScimProvider, capability: str) -> bool:
        """Tell whether the configuration of the service announces a capability, such as ``"sort"``."""
        feature = getattr(provider.config, capability, None)
        return bool(feature and feature.supported)

    def create_users(
        self,
        storage: ScimStorage,
        user_type: ResourceType,
        user_model: Any,
        *user_names: str,
    ) -> list[Any]:
        return [
            storage.create(user_type, user_model(user_name=user_name))
            for user_name in user_names
        ]

    def test_create_fills_the_identifier_and_the_meta(
        self, storage: Any, user_type: ResourceType, user_model: Any
    ) -> None:
        """A created resource gets an id, its resource type, its dates and a version, but no location."""
        (user,) = self.create_users(storage, user_type, user_model, "bjensen")

        assert user.id
        assert user.meta.resource_type == user_type.name
        assert user.meta.created is not None
        assert user.meta.last_modified == user.meta.created
        assert user.meta.version
        assert user.meta.location is None

    def test_create_does_not_change_the_given_resource(
        self, storage: Any, user_type: ResourceType, user_model: Any
    ) -> None:
        """The resource given to create keeps having no id and no meta."""
        given = user_model(user_name="bjensen")
        storage.create(user_type, given)

        assert given.id is None
        assert given.meta is None

    def test_created_resources_get_distinct_identifiers(
        self, storage: Any, user_type: ResourceType, user_model: Any
    ) -> None:
        """Two created resources never share an id."""
        first, second = self.create_users(
            storage, user_type, user_model, "bjensen", "jsmith"
        )
        assert first.id != second.id

    def test_create_returns_a_copy(
        self, storage: Any, user_type: ResourceType, user_model: Any
    ) -> None:
        """Changing a created resource does not change the stored one."""
        (user,) = self.create_users(storage, user_type, user_model, "bjensen")
        user.user_name = "changed"

        assert storage.get(user_type, user.id).user_name == "bjensen"

    def test_get_returns_the_stored_resource(
        self, storage: Any, user_type: ResourceType, user_model: Any
    ) -> None:
        """A stored resource is read back with its values and its meta."""
        (user,) = self.create_users(storage, user_type, user_model, "bjensen")

        stored = storage.get(user_type, user.id)
        assert stored.user_name == "bjensen"
        assert stored.meta.version == user.meta.version

    def test_get_returns_a_copy(
        self, storage: Any, user_type: ResourceType, user_model: Any
    ) -> None:
        """Changing a read resource does not change the stored one."""
        (user,) = self.create_users(storage, user_type, user_model, "bjensen")
        storage.get(user_type, user.id).user_name = "changed"

        assert storage.get(user_type, user.id).user_name == "bjensen"

    def test_get_an_unknown_resource(
        self, storage: Any, user_type: ResourceType
    ) -> None:
        """Reading a resource that does not exist raises a 404."""
        with pytest.raises(NotFoundException):
            storage.get(user_type, "unknown")

    def test_get_a_resource_of_another_type(
        self,
        storage: Any,
        user_type: ResourceType,
        group_type: ResourceType,
        user_model: Any,
    ) -> None:
        """A resource is only found under its own resource type."""
        (user,) = self.create_users(storage, user_type, user_model, "bjensen")

        with pytest.raises(NotFoundException):
            storage.get(group_type, user.id)

    @pytest.mark.parametrize(
        "parameters",
        [
            {"excludedAttributes": "members"},
            {"excludedAttributes": "members.display"},
            {"attributes": "displayName"},
            {"attributes": "members.value"},
        ],
    )
    def test_get_keeps_what_the_response_returns(
        self,
        storage: Any,
        user_type: ResourceType,
        group_type: ResourceType,
        user_model: Any,
        group_model: Any,
        parameters: dict[str, str],
    ) -> None:
        """A resource read with response parameters gives the same response as the whole resource."""
        (user,) = self.create_users(storage, user_type, user_model, "bjensen")
        group = storage.create(
            group_type,
            group_model.model_validate(
                {
                    "displayName": "Admins",
                    "members": [{"value": user.id, "display": "bjensen"}],
                }
            ),
        )
        response_parameters = parametrize(
            ResponseParameters, group_model
        ).model_validate(parameters)

        whole = storage.get(group_type, group.id)
        projected = storage.get(
            group_type, group.id, **projection(storage, response_parameters)
        )

        assert projected.model_dump(
            scim_ctx=Context.RESOURCE_QUERY_RESPONSE,
            response_parameters=response_parameters,
        ) == whole.model_dump(
            scim_ctx=Context.RESOURCE_QUERY_RESPONSE,
            response_parameters=response_parameters,
        )

    def test_update_replaces_the_stored_resource(
        self, storage: Any, user_type: ResourceType, user_model: Any
    ) -> None:
        """An update stores the new state, keeps the creation date, and changes the version."""
        (user,) = self.create_users(storage, user_type, user_model, "bjensen")
        user.display_name = "Barbara"

        updated = storage.update(user_type, user)

        assert updated.display_name == "Barbara"
        assert storage.get(user_type, user.id).display_name == "Barbara"
        assert updated.meta.created == user.meta.created
        assert updated.meta.last_modified >= user.meta.created
        assert updated.meta.version != user.meta.version
        assert updated.meta.location is None

    def test_update_does_not_change_the_given_resource(
        self, storage: Any, user_type: ResourceType, user_model: Any
    ) -> None:
        """The resource given to update keeps its former version."""
        (user,) = self.create_users(storage, user_type, user_model, "bjensen")
        version = user.meta.version

        storage.update(user_type, user)

        assert user.meta.version == version

    def test_update_returns_a_copy(
        self, storage: Any, user_type: ResourceType, user_model: Any
    ) -> None:
        """Changing an updated resource does not change the stored one."""
        (user,) = self.create_users(storage, user_type, user_model, "bjensen")
        storage.update(user_type, user).user_name = "changed"

        assert storage.get(user_type, user.id).user_name == "bjensen"

    def test_update_an_unknown_resource(
        self, storage: Any, user_type: ResourceType, user_model: Any
    ) -> None:
        """Updating a resource that does not exist raises a 404."""
        with pytest.raises(NotFoundException):
            storage.update(user_type, user_model(id="unknown", user_name="bjensen"))

    def test_update_with_the_expected_version(
        self, storage: Any, user_type: ResourceType, user_model: Any
    ) -> None:
        """An update succeeds when the stored version is the expected one."""
        (user,) = self.create_users(storage, user_type, user_model, "bjensen")
        user.display_name = "Barbara"

        storage.update(user_type, user, expected_version=user.meta.version)

        assert storage.get(user_type, user.id).display_name == "Barbara"

    def test_update_with_an_outdated_version(
        self, storage: Any, user_type: ResourceType, user_model: Any
    ) -> None:
        """An update raises a 412 and stores nothing when the stored version changed."""
        (user,) = self.create_users(storage, user_type, user_model, "bjensen")
        outdated = user.meta.version
        user.display_name = "Babs"
        storage.update(user_type, user)
        user.display_name = "Barbara"

        with pytest.raises(PreconditionFailedException):
            storage.update(user_type, user, expected_version=outdated)
        assert storage.get(user_type, user.id).display_name == "Babs"

    def test_delete_removes_the_resource(
        self, storage: Any, user_type: ResourceType, user_model: Any
    ) -> None:
        """A deleted resource cannot be read anymore."""
        (user,) = self.create_users(storage, user_type, user_model, "bjensen")

        storage.delete(user_type, user.id)

        with pytest.raises(NotFoundException):
            storage.get(user_type, user.id)

    def test_delete_an_unknown_resource(
        self, storage: Any, user_type: ResourceType
    ) -> None:
        """Deleting a resource that does not exist raises a 404."""
        with pytest.raises(NotFoundException):
            storage.delete(user_type, "unknown")

    def test_delete_with_the_expected_version(
        self, storage: Any, user_type: ResourceType, user_model: Any
    ) -> None:
        """A deletion succeeds when the stored version is the expected one."""
        (user,) = self.create_users(storage, user_type, user_model, "bjensen")

        storage.delete(user_type, user.id, expected_version=user.meta.version)

        with pytest.raises(NotFoundException):
            storage.get(user_type, user.id)

    def test_delete_with_an_outdated_version(
        self, storage: Any, user_type: ResourceType, user_model: Any
    ) -> None:
        """A deletion raises a 412 and keeps the resource when the stored version changed."""
        (user,) = self.create_users(storage, user_type, user_model, "bjensen")
        outdated = user.meta.version
        user.display_name = "Babs"
        storage.update(user_type, user)

        with pytest.raises(PreconditionFailedException):
            storage.delete(user_type, user.id, expected_version=outdated)
        assert storage.get(user_type, user.id).user_name == "bjensen"

    def test_create_with_a_taken_unique_value(
        self, storage: Any, user_type: ResourceType, user_model: Any
    ) -> None:
        """A userName already taken, whatever its case, raises a 409 on creation."""
        self.create_users(storage, user_type, user_model, "bjensen")

        with pytest.raises(UniquenessException):
            storage.create(user_type, user_model(user_name="BJensen"))

    def test_update_with_a_taken_unique_value(
        self, storage: Any, user_type: ResourceType, user_model: Any
    ) -> None:
        """A userName already taken by another resource raises a 409 on update."""
        _, user = self.create_users(storage, user_type, user_model, "bjensen", "jsmith")
        user.user_name = "bjensen"

        with pytest.raises(UniquenessException):
            storage.update(user_type, user)
        assert storage.get(user_type, user.id).user_name == "jsmith"

    def test_search_a_resource_type(
        self,
        storage: Any,
        user_type: ResourceType,
        group_type: ResourceType,
        user_model: Any,
        group_model: Any,
    ) -> None:
        """A search on a resource type returns its resources only, and counts them."""
        self.create_users(storage, user_type, user_model, "alice", "bob")
        storage.create(group_type, group_model(display_name="admins"))

        page = self.search(storage, [user_type], self.search_request([user_model]))

        assert page.total == 2
        assert sorted(r.user_name for r in page.resources) == ["alice", "bob"]

    def test_search_returns_copies(
        self, storage: Any, user_type: ResourceType, user_model: Any
    ) -> None:
        """Changing a found resource does not change the stored one."""
        (user,) = self.create_users(storage, user_type, user_model, "bjensen")
        (found,) = self.search(
            storage, [user_type], self.search_request([user_model])
        ).resources
        found.user_name = "changed"

        assert storage.get(user_type, user.id).user_name == "bjensen"

    def test_search_pages_the_results(
        self, storage: Any, user_type: ResourceType, user_model: Any
    ) -> None:
        """A page holds count resources from startIndex, and the total counts them all."""
        self.create_users(storage, user_type, user_model, "alice", "bob", "carol")

        page = self.search(
            storage,
            [user_type],
            self.search_request([user_model], start_index=2, count=1),
        )

        assert page.total == 3
        assert len(page.resources) == 1

    def test_search_without_count_returns_every_resource(
        self, storage: Any, user_type: ResourceType, user_model: Any
    ) -> None:
        """A search without count is not paged."""
        self.create_users(storage, user_type, user_model, "alice", "bob", "carol")

        page = self.search(storage, [user_type], self.search_request([user_model]))

        assert page.total == 3
        assert len(page.resources) == 3

    def test_search_with_a_filter(
        self,
        storage: Any,
        provider: ScimProvider,
        user_type: ResourceType,
        user_model: Any,
    ) -> None:
        """A filter keeps the matching resources, and the total counts them only."""
        self.require(self.announces(provider, "filter"), "filtering")
        self.create_users(storage, user_type, user_model, "alice", "bob", "carol")

        page = self.search(
            storage,
            [user_type],
            self.search_request([user_model], filter='userName eq "bob"', count=10),
        )

        assert page.total == 1
        assert [r.user_name for r in page.resources] == ["bob"]

    def test_search_sorted(
        self,
        storage: Any,
        provider: ScimProvider,
        user_type: ResourceType,
        user_model: Any,
    ) -> None:
        """A sorted search orders the resources before paging them."""
        self.require(self.announces(provider, "sort"), "sorting")
        self.create_users(storage, user_type, user_model, "bob", "carol", "alice")

        page = self.search(
            storage,
            [user_type],
            self.search_request(
                [user_model], sort_by="userName", sort_order="descending", count=2
            ),
        )

        assert [r.user_name for r in page.resources] == ["carol", "bob"]

    def test_search_at_the_root(
        self,
        storage: Any,
        user_type: ResourceType,
        group_type: ResourceType,
        user_model: Any,
        group_model: Any,
    ) -> None:
        """A search on several resource types returns their resources as one collection."""
        self.require(self.supports_root_search, "searching at the root")
        self.create_users(storage, user_type, user_model, "alice")
        storage.create(group_type, group_model(display_name="admins"))

        page = self.search(
            storage,
            [user_type, group_type],
            self.search_request([user_model, group_model]),
        )

        assert page.total == 2
        assert {r.meta.resource_type for r in page.resources} == {
            user_type.name,
            group_type.name,
        }

    def test_search_at_the_root_on_an_attribute_some_types_lack(
        self,
        storage: Any,
        provider: ScimProvider,
        user_type: ResourceType,
        group_type: ResourceType,
        user_model: Any,
        group_model: Any,
    ) -> None:
        """An attribute a resource type does not declare matches none of its resources."""
        self.require(self.supports_root_search, "searching at the root")
        self.require(self.announces(provider, "filter"), "filtering")
        self.create_users(storage, user_type, user_model, "alice")
        storage.create(group_type, group_model(display_name="admins"))

        page = self.search(
            storage,
            [user_type, group_type],
            self.search_request([user_model, group_model], filter="userName pr"),
        )

        assert page.total == 1
        assert page.resources[0].user_name == "alice"

    def walk(
        self,
        storage: Any,
        resource_types: list[ResourceType],
        search_request: SearchRequest[Any],
    ) -> list[Any]:
        """Return every page of a search paged with a cursor, from the first one."""
        self.require(storage.supports_cursors, "cursor pagination")
        pages = [self.search(storage, resource_types, search_request)]
        while pages[-1].next is not None:
            pages.append(
                self.search(storage, resource_types, search_request, pages[-1].next)
            )
        return pages

    def walk_back(
        self,
        storage: Any,
        resource_types: list[ResourceType],
        search_request: SearchRequest[Any],
        page: Any,
    ) -> list[Any]:
        """Return the pages before a page, from the closest one, following the previous cursors."""
        pages = []
        while page.previous is not None:
            page = self.search(storage, resource_types, search_request, page.previous)
            pages.append(page)
        return pages

    @staticmethod
    def ids(pages: list[Any]) -> list[str]:
        return [resource.id for page in pages for resource in page.resources]

    def test_cursor_pages_every_resource_once(
        self, storage: Any, user_type: ResourceType, user_model: Any
    ) -> None:
        """Following the next cursors returns every resource once, count by count."""
        names = ["alice", "bob", "carol", "dave", "erin"]
        self.create_users(storage, user_type, user_model, *names)

        pages = self.walk(
            storage, [user_type], self.search_request([user_model], cursor="", count=2)
        )

        assert [len(page.resources) for page in pages] == [2, 2, 1]
        assert sorted(r.user_name for page in pages for r in page.resources) == names
        assert pages[0].previous is None
        assert {page.total for page in pages} <= {5, None}

    def test_cursor_pages_in_sort_order(
        self,
        storage: Any,
        provider: ScimProvider,
        user_type: ResourceType,
        user_model: Any,
    ) -> None:
        """The pages of a sorted search follow each other in the sort order."""
        self.require(self.announces(provider, "sort"), "sorting")
        self.create_users(
            storage, user_type, user_model, "dave", "alice", "carol", "bob"
        )

        pages = self.walk(
            storage,
            [user_type],
            self.search_request(
                [user_model],
                cursor="",
                count=3,
                sort_by="userName",
                sort_order="descending",
            ),
        )

        assert [r.user_name for page in pages for r in page.resources] == [
            "dave",
            "carol",
            "bob",
            "alice",
        ]

    def test_cursor_pages_equal_sort_values_once(
        self,
        storage: Any,
        provider: ScimProvider,
        user_type: ResourceType,
        user_model: Any,
    ) -> None:
        """Resources with the same sort value, or without one, are returned once, even across pages."""
        self.require(self.announces(provider, "sort"), "sorting")
        for user_name, nick_name in [
            ("alice", "twin"),
            ("bob", None),
            ("carol", "twin"),
            ("dave", "twin"),
            ("erin", None),
        ]:
            storage.create(
                user_type, user_model(user_name=user_name, nick_name=nick_name)
            )

        pages = self.walk(
            storage,
            [user_type],
            self.search_request([user_model], cursor="", count=2, sort_by="nickName"),
        )

        names = [r.user_name for page in pages for r in page.resources]
        assert sorted(names[:3]) == ["alice", "carol", "dave"]
        assert sorted(names[3:]) == ["bob", "erin"]

    def test_cursor_pages_back(
        self, storage: Any, user_type: ResourceType, user_model: Any
    ) -> None:
        """Following the previous cursors from the last page returns the same pages, back to the first."""
        self.create_users(
            storage, user_type, user_model, "alice", "bob", "carol", "dave", "erin"
        )
        search_request = self.search_request([user_model], cursor="", count=2)
        forward = self.walk(storage, [user_type], search_request)

        backward = self.walk_back(storage, [user_type], search_request, forward[-1])

        assert [[r.id for r in page.resources] for page in reversed(backward)] == [
            [r.id for r in page.resources] for page in forward[:-1]
        ]
        assert backward[-1].previous is None

    @pytest.mark.parametrize("deleted", ["last of the page", "first of the page"])
    def test_cursor_gives_stable_pages_when_a_resource_of_the_page_is_deleted(
        self, storage: Any, user_type: ResourceType, user_model: Any, deleted: str
    ) -> None:
        """Deleting a resource already returned does not skip nor repeat the next ones."""
        self.create_users(
            storage, user_type, user_model, "alice", "bob", "carol", "dave", "erin"
        )
        search_request = self.search_request([user_model], cursor="", count=2)
        expected = self.ids(self.walk(storage, [user_type], search_request))
        first = self.search(storage, [user_type], search_request)
        victim = first.resources[-1 if deleted == "last of the page" else 0]

        storage.delete(user_type, victim.id)
        rest = [self.search(storage, [user_type], search_request, first.next)]
        while rest[-1].next is not None:
            rest.append(
                self.search(storage, [user_type], search_request, rest[-1].next)
            )

        assert self.ids([first, *rest]) == expected

    def test_cursor_gives_stable_pages_when_a_resource_is_created(
        self, storage: Any, user_type: ResourceType, user_model: Any
    ) -> None:
        """Creating a resource between two pages does not skip nor repeat the existing ones."""
        self.create_users(
            storage, user_type, user_model, "alice", "bob", "carol", "dave", "erin"
        )
        search_request = self.search_request([user_model], cursor="", count=2)
        expected = self.ids(self.walk(storage, [user_type], search_request))
        first = self.search(storage, [user_type], search_request)

        (created,) = self.create_users(storage, user_type, user_model, "frank")
        rest = [self.search(storage, [user_type], search_request, first.next)]
        while rest[-1].next is not None:
            rest.append(
                self.search(storage, [user_type], search_request, rest[-1].next)
            )

        returned = self.ids([first, *rest])
        assert [i for i in returned if i != created.id] == expected

    def test_cursor_pages_back_after_a_deletion(
        self, storage: Any, user_type: ResourceType, user_model: Any
    ) -> None:
        """Deleting the first resource of a page does not change the page before it."""
        self.create_users(
            storage, user_type, user_model, "alice", "bob", "carol", "dave", "erin"
        )
        search_request = self.search_request([user_model], cursor="", count=2)
        forward = self.walk(storage, [user_type], search_request)

        storage.delete(user_type, forward[1].resources[0].id)
        previous = self.search(
            storage, [user_type], search_request, forward[1].previous
        )

        assert self.ids([previous]) == self.ids([forward[0]])
        assert previous.previous is None

    def test_cursor_with_count_zero(
        self, storage: Any, user_type: ResourceType, user_model: Any
    ) -> None:
        """A count of 0 returns no resource and no cursor, so the client does not loop."""
        self.require(storage.supports_cursors, "cursor pagination")
        self.create_users(storage, user_type, user_model, "alice", "bob")

        page = self.search(
            storage, [user_type], self.search_request([user_model], cursor="", count=0)
        )

        assert page.resources == []
        assert page.next is None
        assert page.previous is None
        assert page.total in (2, None)

    def test_cursor_at_the_root(
        self,
        storage: Any,
        user_type: ResourceType,
        group_type: ResourceType,
        user_model: Any,
        group_model: Any,
    ) -> None:
        """A cursor pages the resources of several resource types as one collection."""
        self.require(self.supports_root_search, "searching at the root")
        users = self.create_users(storage, user_type, user_model, "alice", "bob")
        group = storage.create(group_type, group_model(display_name="admins"))

        pages = self.walk(
            storage,
            [user_type, group_type],
            self.search_request([user_model, group_model], cursor="", count=1),
        )

        assert sorted(self.ids(pages)) == sorted([users[0].id, users[1].id, group.id])

    def test_operation(
        self, storage: Any, user_type: ResourceType, user_model: Any
    ) -> None:
        """The operation context encloses the calls of one SCIM operation."""
        with storage.operation():
            (user,) = self.create_users(storage, user_type, user_model, "bjensen")
            storage.get(user_type, user.id)

    def test_operation_lets_exceptions_through(self, storage: Any) -> None:
        """An exception raised within an operation is not swallowed."""
        with pytest.raises(RuntimeError), storage.operation():
            raise RuntimeError


class BlockingStorage(ScimStorage):
    """Run the coroutines of an asynchronous storage one by one, for the contract tests."""

    def __init__(self, storage: AsyncScimStorage, runner: asyncio.Runner) -> None:
        self.storage = storage
        self.runner = runner
        self.supports_cursors = storage.supports_cursors

    def run(self, coroutine: Coroutine[Any, Any, T]) -> T:
        return self.runner.run(coroutine)

    def get(
        self,
        resource_type: ResourceType,
        resource_id: str,
        *,
        response_parameters: ResponseParameters[Any] | None = None,
    ) -> Resource[Any]:
        return self.run(
            self.storage.get(
                resource_type,
                resource_id,
                **projection(self.storage, response_parameters),
            )
        )

    def search(
        self,
        resource_types: list[ResourceType],
        search_request: SearchRequest[Any],
        *,
        position: Any = None,
    ) -> SearchPage:
        return self.run(
            async_search_page(self.storage, resource_types, search_request, position)
        )

    def create(
        self, resource_type: ResourceType, resource: Resource[Any]
    ) -> Resource[Any]:
        return self.run(self.storage.create(resource_type, resource))

    def update(
        self,
        resource_type: ResourceType,
        resource: Resource[Any],
        *,
        expected_version: str | None = None,
    ) -> Resource[Any]:
        return self.run(
            self.storage.update(
                resource_type, resource, expected_version=expected_version
            )
        )

    def delete(
        self,
        resource_type: ResourceType,
        resource_id: str,
        *,
        expected_version: str | None = None,
    ) -> None:
        self.run(
            self.storage.delete(
                resource_type, resource_id, expected_version=expected_version
            )
        )

    @contextmanager
    def operation(self) -> Generator[None]:
        manager = self.storage.operation()
        self.run(manager.__aenter__())
        try:
            yield
        except BaseException as exception:
            self.run(
                manager.__aexit__(type(exception), exception, exception.__traceback__)
            )
            raise
        self.run(manager.__aexit__(None, None, None))


class AsyncScimStorageContract(ScimStorageContract):
    """The rules every :class:`~scim2_server.storage.AsyncScimStorage` follows.

    These are the rules of :class:`ScimStorageContract`. Each test runs the
    coroutines of the storage on a single event loop, one after the other.
    """

    @pytest.fixture
    def storage(self, async_storage: AsyncScimStorage) -> Iterator[ScimStorage]:
        with asyncio.Runner() as runner:
            yield BlockingStorage(async_storage, runner)
