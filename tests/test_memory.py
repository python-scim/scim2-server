import datetime
from typing import Annotated

import pytest
from scim2_models import URN
from scim2_models import Attribute
from scim2_models import CaseExact
from scim2_models import Extension
from scim2_models import Filter
from scim2_models import Resource
from scim2_models import ResourceType
from scim2_models import Schema
from scim2_models import ScimProvider
from scim2_models import SearchRequest
from scim2_models import Sort
from scim2_models import Uniqueness
from scim2_models import UniquenessException

from scim2_server.memory import AsyncInMemoryStorage
from scim2_server.memory import InMemoryStorage
from scim2_server.testing import AsyncScimStorageContract
from scim2_server.testing import ScimStorageContract
from scim2_server.utils import load_default_service_provider_config


class TestInMemoryStorage(ScimStorageContract):
    @pytest.fixture
    def storage(self):
        return InMemoryStorage()


class TestAsyncInMemoryStorage(AsyncScimStorageContract):
    @pytest.fixture
    def async_storage(self):
        return AsyncInMemoryStorage()


def test_async_storage_serves_the_resources_of_a_given_storage(
    user_type, scim_provider
):
    """An asynchronous storage serves the resources of the storage it is given."""
    storage = InMemoryStorage()
    User = scim_provider.model_for(user_type)
    storage.create(user_type, User(user_name="bjensen"))

    assert AsyncInMemoryStorage(storage).resources == storage.resources


class TestInMemoryStorageWithoutSearchFeatures(ScimStorageContract):
    """The contract skips the features a service does not announce."""

    supports_root_search = False

    @pytest.fixture
    def storage(self):
        return InMemoryStorage()

    @pytest.fixture
    def provider(self, scim_provider):
        config = load_default_service_provider_config()
        config.filter = Filter(supported=False)
        config.sort = Sort(supported=False)
        return ScimProvider(
            models=scim_provider.models,
            resource_types=scim_provider.resource_types,
            config=config,
        )


def test_identifiers_can_be_predictable(user_type, scim_provider):
    """Overriding generate_id gives predictable identifiers."""

    class PredictableStorage(InMemoryStorage):
        def generate_id(self, resource_type, resource):
            return f"{resource_type.id}-{len(self.resources)}"

    storage = PredictableStorage()
    User = scim_provider.model_for(user_type)
    assert storage.create(user_type, User(user_name="a")).id == "User-0"
    assert storage.create(user_type, User(user_name="b")).id == "User-1"


def test_dates_come_from_the_clock(user_type, scim_provider):
    """The dates of the resources come from the given clock."""
    now = datetime.datetime(2024, 3, 14, 6, 0, tzinfo=datetime.UTC)
    storage = InMemoryStorage(clock=lambda: now)
    User = scim_provider.model_for(user_type)

    user = storage.create(user_type, User(user_name="bjensen"))

    assert user.meta.created == now
    assert storage.update(user_type, user).meta.last_modified == now


def test_versions_differ_within_the_same_instant(user_type, scim_provider):
    """Two writes at the same date still get distinct versions."""
    now = datetime.datetime(2024, 3, 14, 6, 0, tzinfo=datetime.UTC)
    storage = InMemoryStorage(clock=lambda: now)
    User = scim_provider.model_for(user_type)

    user = storage.create(user_type, User(user_name="bjensen"))
    updated = storage.update(user_type, user)

    assert updated.meta.version != user.meta.version


def test_operations_can_be_nested(user_type, scim_provider):
    """The lock of an operation can be taken again inside it."""
    storage = InMemoryStorage()
    User = scim_provider.model_for(user_type)
    with storage.operation(), storage.operation():
        storage.create(user_type, User(user_name="bjensen"))


def test_uniqueness_follows_the_case_exactness_of_each_attribute():
    """A case-exact unique attribute tells values apart by case, others do not, extensions included."""
    foo_schema = Schema(
        id="urn:example:2.0:Foo",
        name="Foo",
        attributes=[
            Attribute(
                name="a",
                type=Attribute.Type.string,
                uniqueness=Uniqueness.server,
                case_exact=CaseExact.true,
            ),
        ],
    )
    bar_schema = Schema(
        id="urn:example:2.0:Bar",
        name="Bar",
        attributes=[
            Attribute(
                name="a",
                type=Attribute.Type.string,
                uniqueness=Uniqueness.global_,
            ),
        ],
    )
    FooBar = Resource.from_schema(foo_schema)[Extension.from_schema(bar_schema)]
    foo_bar_type = ResourceType.from_resource(FooBar)
    storage = InMemoryStorage()

    def create(foo, bar):
        payload = {"a": foo, "urn:example:2.0:Bar": {"a": bar}}
        storage.create(foo_bar_type, FooBar.model_validate(payload))

    create("ABC", "DEF")
    create("abc", "GHI")
    with pytest.raises(UniquenessException):
        create("ABC", "JKL")
    with pytest.raises(UniquenessException):
        create("XYZ", "def")


def test_only_the_user_name_of_the_default_user_is_unique(user_type, scim_provider):
    """Two users sharing every value but their userName do not conflict."""
    storage = InMemoryStorage()
    User = scim_provider.model_for(user_type)
    payload = {"displayName": "Babs", "externalId": "1", "nickName": "bj"}
    storage.create(user_type, User.model_validate({"userName": "bjensen", **payload}))
    storage.create(user_type, User.model_validate({"userName": "jsmith", **payload}))
    with pytest.raises(UniquenessException):
        storage.create(user_type, User(user_name="bjensen"))


def test_a_missing_unique_value_does_not_clash():
    """Two resources lacking a unique value do not conflict, as SQL NULLs do not."""

    class Badge(Resource):
        __schema__ = URN("urn:example:2.0:Badge")
        code: Annotated[str | None, Uniqueness.server] = None

    storage = InMemoryStorage()
    badge_type = ResourceType.from_resource(Badge)
    storage.create(badge_type, Badge())
    storage.create(badge_type, Badge())
    storage.create(badge_type, Badge(code="x"))
    with pytest.raises(UniquenessException):
        storage.create(badge_type, Badge(code="x"))


def test_unique_values_are_compared_with_unicode_case_folding(user_type, scim_provider):
    """Unicode case folding makes "Straße" and "STRASSE" the same value."""
    storage = InMemoryStorage()
    User = scim_provider.model_for(user_type)
    storage.create(user_type, User(user_name="Straße"))
    with pytest.raises(UniquenessException):
        storage.create(user_type, User(user_name="STRASSE"))


def test_uniqueness_does_not_span_schemas():
    """Two schemas declaring a unique attribute of the same name do not constrain each other."""

    class Badge(Resource):
        __schema__ = URN("urn:example:2.0:Badge")
        code: Annotated[str | None, Uniqueness.server] = None

    class Token(Resource):
        __schema__ = URN("urn:example:2.0:Token")
        code: Annotated[str | None, Uniqueness.server] = None

    storage = InMemoryStorage()
    storage.create(ResourceType.from_resource(Badge), Badge(code="x"))
    storage.create(ResourceType.from_resource(Token), Token(code="x"))


def test_uniqueness_of_an_extension_spans_the_resources_it_extends():
    """An extension attribute is unique among every resource carrying the extension."""

    class Tag(Extension):
        __schema__ = URN("urn:example:2.0:Tag")
        code: Annotated[str | None, Uniqueness.server] = None

    class Badge(Resource):
        __schema__ = URN("urn:example:2.0:Badge")

    class Token(Resource):
        __schema__ = URN("urn:example:2.0:Token")

    storage = InMemoryStorage()
    storage.create(
        ResourceType.from_resource(Badge[Tag]),
        Badge[Tag].model_validate({"urn:example:2.0:Tag": {"code": "x"}}),
    )
    storage.create(ResourceType.from_resource(Token), Token())
    with pytest.raises(UniquenessException):
        storage.create(
            ResourceType.from_resource(Token[Tag]),
            Token[Tag].model_validate({"urn:example:2.0:Tag": {"code": "x"}}),
        )


@pytest.mark.parametrize("root", [False, True])
def test_a_filter_bound_to_no_model_is_resolved_against_the_stored_resources(
    user_type, scim_provider, root
):
    """A filter left unbound is resolved against the models of the stored resources."""
    storage = InMemoryStorage()
    User = scim_provider.model_for(user_type)
    for user_name in ("alice", "bob"):
        storage.create(user_type, User(user_name=user_name))
    resource_types = list(scim_provider.resource_types) if root else [user_type]

    total, resources = storage.search(
        resource_types, SearchRequest(filter='userName eq "bob"')
    )

    assert total == 1
    assert resources[0].user_name == "bob"


def test_an_unbound_filter_without_resource(user_type):
    """With no stored resource, an unbound filter has nothing to be resolved against nor to match."""
    storage = InMemoryStorage()
    assert storage.search([user_type], SearchRequest(filter='userName eq "bob"')) == (
        0,
        [],
    )


def test_a_resource_type_named_apart_from_its_id(static_data):
    """The resources of a resource type whose name differs from its id stay reachable."""
    resource_type = ResourceType(
        id="Usr",
        name="User",
        endpoint="/Users",
        schema="urn:ietf:params:scim:schemas:core:2.0:User",
    )
    provider = ScimProvider.from_discovery(static_data[0].values(), [resource_type])
    storage = InMemoryStorage()
    User = provider.model_for(resource_type)
    created = storage.create(resource_type, User(user_name="bjensen"))
    assert created.meta.resource_type == "User"

    assert storage.get(resource_type, created.id).user_name == "bjensen"
    assert storage.search([resource_type], SearchRequest())[0] == 1
    with pytest.raises(UniquenessException):
        storage.create(resource_type, User(user_name="bjensen"))
