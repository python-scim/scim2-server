"""A test suite checking that a storage follows the :class:`~scim2_server.storage.ScimStorage` contract.

Subclass :class:`ScimStorageContract` in your own tests, and give it a
``storage`` fixture returning a new, empty storage::

    from scim2_server.testing import ScimStorageContract


    class TestMyStorage(ScimStorageContract):
        @pytest.fixture
        def storage(self):
            return MyStorage()

The suite needs the ``testing`` extra, which installs pytest.
"""

from typing import Any
from typing import Union
from typing import cast

import pytest
from scim2_models import NotFoundException
from scim2_models import PreconditionFailedException
from scim2_models import Resource
from scim2_models import ResourceType
from scim2_models import ScimProvider
from scim2_models import SearchRequest
from scim2_models import UniquenessException

from scim2_server.storage import ScimStorage
from scim2_server.utils import load_default_provider
from scim2_server.utils import parametrize


class ScimStorageContract:
    """The rules every :class:`~scim2_server.storage.ScimStorage` follows.

    The suite uses the ``User`` and ``Group`` resource types of the
    ``provider`` fixture, which serves the default resource types of
    :rfc:`RFC 7643 <7643>` unless overridden. The tests of a feature that the
    configuration of the provider does not announce, such as sorting, are
    skipped.
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
        storage.update(user_type, user)
        user.display_name = "Barbara"

        with pytest.raises(PreconditionFailedException):
            storage.update(user_type, user, expected_version=outdated)
        assert storage.get(user_type, user.id).display_name is None

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

        total, resources = storage.search(
            [user_type], self.search_request([user_model])
        )

        assert total == 2
        assert sorted(r.user_name for r in resources) == ["alice", "bob"]

    def test_search_returns_copies(
        self, storage: Any, user_type: ResourceType, user_model: Any
    ) -> None:
        """Changing a found resource does not change the stored one."""
        (user,) = self.create_users(storage, user_type, user_model, "bjensen")
        _, (found,) = storage.search([user_type], self.search_request([user_model]))
        found.user_name = "changed"

        assert storage.get(user_type, user.id).user_name == "bjensen"

    def test_search_pages_the_results(
        self, storage: Any, user_type: ResourceType, user_model: Any
    ) -> None:
        """A page holds count resources from startIndex, and the total counts them all."""
        self.create_users(storage, user_type, user_model, "alice", "bob", "carol")

        total, resources = storage.search(
            [user_type], self.search_request([user_model], start_index=2, count=1)
        )

        assert total == 3
        assert len(resources) == 1

    def test_search_without_count_returns_every_resource(
        self, storage: Any, user_type: ResourceType, user_model: Any
    ) -> None:
        """A search without count is not paged."""
        self.create_users(storage, user_type, user_model, "alice", "bob", "carol")

        total, resources = storage.search(
            [user_type], self.search_request([user_model])
        )

        assert total == 3
        assert len(resources) == 3

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

        total, resources = storage.search(
            [user_type],
            self.search_request([user_model], filter='userName eq "bob"', count=10),
        )

        assert total == 1
        assert [r.user_name for r in resources] == ["bob"]

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

        _, resources = storage.search(
            [user_type],
            self.search_request(
                [user_model], sort_by="userName", sort_order="descending", count=2
            ),
        )

        assert [r.user_name for r in resources] == ["carol", "bob"]

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

        total, resources = storage.search(
            [user_type, group_type], self.search_request([user_model, group_model])
        )

        assert total == 2
        assert {r.meta.resource_type for r in resources} == {
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

        total, resources = storage.search(
            [user_type, group_type],
            self.search_request([user_model, group_model], filter="userName pr"),
        )

        assert total == 1
        assert resources[0].user_name == "alice"

    def test_operation(
        self, storage: Any, user_type: ResourceType, user_model: Any
    ) -> None:
        """The operation context encloses the calls of one SCIM operation."""
        with storage.operation():
            (user,) = self.create_users(storage, user_type, user_model, "bjensen")
            storage.get(user_type, user.id)
