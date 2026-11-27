import datetime
import inspect
from typing import Annotated

import pytest
from pydantic import ValidationError
from scim2_models import URN
from scim2_models import Attribute
from scim2_models import CaseExact
from scim2_models import ComplexAttribute
from scim2_models import Extension
from scim2_models import Filter
from scim2_models import InvalidCursorException
from scim2_models import InvalidFilterException
from scim2_models import InvalidPathException
from scim2_models import Meta
from scim2_models import Mutability
from scim2_models import NotFoundException
from scim2_models import Resource
from scim2_models import ResourceType
from scim2_models import Returned
from scim2_models import Schema
from scim2_models import ScimPolicy
from scim2_models import ScimProvider
from scim2_models import SearchRequest
from scim2_models import Sort
from scim2_models import Uniqueness
from scim2_models import UniquenessException
from scim2_models import default_comparison_key

from scim2_server.memory import AsyncInMemoryStorage
from scim2_server.memory import InMemoryStorage
from scim2_server.storage import SearchPage
from scim2_server.testing import AsyncScimStorageContract
from scim2_server.testing import Mailbox
from scim2_server.testing import ScimStorageContract
from scim2_server.utils import load_default_resource_types
from scim2_server.utils import load_default_schemas
from scim2_server.utils import load_default_service_provider_config


class TestInMemoryStorage(ScimStorageContract):
    stores_mailboxes = True

    @pytest.fixture
    def storage(self):
        return InMemoryStorage()


class TestAsyncInMemoryStorage(AsyncScimStorageContract):
    stores_mailboxes = True

    @pytest.fixture
    def async_storage(self):
        return AsyncInMemoryStorage()


class StorageWithoutDates(InMemoryStorage):
    """Leave out the dates the storage does not store."""

    def __init__(self, stores_created, stores_last_modified):
        super().__init__()
        self.stores_created = stores_created
        self.stores_last_modified = stores_last_modified

    def _store(self, resource, values):
        if not self.stores_created:
            resource.meta.created = None
        if not self.stores_last_modified:
            resource.meta.last_modified = None
        super()._store(resource, values)


class TestStorageWithoutDates(ScimStorageContract):
    """The contract checks that a storage without dates leaves them empty."""

    stores_created = False
    stores_last_modified = False

    @pytest.fixture
    def storage(self):
        return StorageWithoutDates(self.stores_created, self.stores_last_modified)


class TestStorageWithoutCreationDate(TestStorageWithoutDates):
    """The contract checks the modification date of a storage without creation date."""

    stores_created = False
    stores_last_modified = True


class TestStorageWithoutModificationDate(TestStorageWithoutDates):
    """The contract checks the creation date of a storage without modification date."""

    stores_created = True
    stores_last_modified = False


class StorageWithoutPassword(InMemoryStorage):
    """Drop the password of the users."""

    def _store(self, resource, values):
        if hasattr(resource, "password"):
            resource.password = None

        super()._store(resource, values)


class TestStorageWithoutPassword(ScimStorageContract):
    """The contract skips the password checks for a storage without password."""

    stores_password = False

    @pytest.fixture
    def storage(self):
        return StorageWithoutPassword()


class StorageNumberedByType(InMemoryStorage):
    """Number the resources of each resource type from 1."""

    def __init__(self):
        super().__init__()
        self.counts = {}

    def generate_id(self, resource_type, resource):
        self.counts[resource_type.name] = self.counts.get(resource_type.name, 0) + 1
        return str(self.counts[resource_type.name])


class TestStorageNumberedByType(ScimStorageContract):
    """The contract holds when resources of different types share an identifier."""

    @pytest.fixture
    def storage(self):
        return StorageNumberedByType()


@pytest.fixture
def numbered(scim_provider):
    """Return a storage holding two users and a group, numbered from 1 by resource type, and the types to search at the root."""
    types = {rt.id: rt for rt in scim_provider.resource_types}
    user_type, group_type = types["User"], types["Group"]
    storage = StorageNumberedByType()
    User = scim_provider.model_for(user_type)
    Group = scim_provider.model_for(group_type)
    storage.create(user_type, User(user_name="alice"))
    storage.create(user_type, User(user_name="bob"))
    storage.create(group_type, Group(display_name="admins"))
    return storage, [group_type, user_type]


@pytest.mark.parametrize("cursor", [None, ""], ids=["by index", "by cursor"])
def test_a_root_search_orders_tied_resources_by_resource_type(numbered, cursor):
    """At the root, resources with the same sort value come by resource type, in the order of the search, then by identifier."""
    storage, resource_types = numbered

    page = storage.search(resource_types, SearchRequest(cursor=cursor))

    assert [(r.meta.resource_type, r.id) for r in page.resources] == [
        ("Group", "1"),
        ("User", "1"),
        ("User", "2"),
    ]


@pytest.mark.parametrize(
    "position",
    [
        {"d": "next", "k": None, "i": "1"},
        {"d": "next", "k": None, "t": 2, "i": "1"},
    ],
)
def test_a_root_position_needs_one_of_the_searched_resource_types(numbered, position):
    """At the root, a position without the index of a searched resource type raises invalidCursor."""
    storage, resource_types = numbered

    with pytest.raises(InvalidCursorException):
        storage.search(
            resource_types, SearchRequest(cursor="", count=1), position=position
        )


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


class StorageWithoutTitle(InMemoryStorage):
    """Refuse to filter or to sort on the title, as a storage that does not store it."""

    def search(self, resource_types, search_request, *, position=None):
        if search_request.filter is not None and "title" in str(search_request.filter):
            raise InvalidFilterException(detail="Cannot filter on 'title'")
        if str(search_request.sort_by) == "title":
            raise InvalidPathException(detail="Cannot sort on 'title'")
        return super().search(resource_types, search_request, position=position)


class TestStorageRefusingSomeSearches(ScimStorageContract):
    """The contract skips the filters and the sorts a storage refuses."""

    @pytest.fixture
    def storage(self):
        return StorageWithoutTitle()


class StorageRefusingEmptyGroups(InMemoryStorage):
    """Refuse groups without members."""

    def create(self, resource_type, resource):
        assert resource_type.id != "Group" or resource.members
        return super().create(resource_type, resource)


class TestStorageRefusingEmptyGroups(ScimStorageContract):
    """The contract only creates groups with members."""

    @pytest.fixture
    def storage(self):
        return StorageRefusingEmptyGroups()


class TestModelsWithoutSomeAttributes(ScimStorageContract):
    """The contract skips the filters and the sorts the models refuse."""

    @pytest.fixture
    def storage(self):
        return InMemoryStorage()

    @pytest.fixture
    def provider(self):
        schemas = load_default_schemas()
        user = schemas["urn:ietf:params:scim:schemas:core:2.0:User"]
        user.attributes = [a for a in user.attributes if a.name != "nickName"]
        emails = next(a for a in user.attributes if a.name == "emails")
        emails.sub_attributes = [
            a for a in emails.sub_attributes if a.name not in ("type", "primary")
        ]
        return ScimProvider.from_discovery(
            schemas.values(),
            load_default_resource_types().values(),
            config=load_default_service_provider_config(),
        )

    def search_users(self):
        return [
            {
                **user,
                "emails": [{"value": email["value"]} for email in user["emails"]],
            }
            if "emails" in user
            else user
            for user in super().search_users()
        ]


def test_the_contract_does_not_skip_other_invalid_searches(user_type, scim_provider):
    """A search request invalid for another reason than an undeclared attribute fails."""
    User = scim_provider.model_for(user_type)
    with pytest.raises(ValidationError):
        ScimStorageContract().search_request_or_skip([User], count="many")


class StorageWithoutCursors:
    supports_cursors = False


@pytest.mark.parametrize(
    "name",
    [name for name in dir(ScimStorageContract) if name.startswith("test_cursor_")],
)
def test_the_cursor_tests_skip_before_creating_resources(name):
    """The cursor tests skip a storage without cursors before they create any resource."""
    test = getattr(ScimStorageContract(), name)
    parameters = dict.fromkeys(inspect.signature(test).parameters)
    parameters["storage"] = StorageWithoutCursors()

    with pytest.raises(pytest.skip.Exception):
        test(**parameters)


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


def found_user_names(storage, user_type, User, scim_filter):
    page = storage.search([user_type], SearchRequest[User](filter=scim_filter))
    return sorted(resource.user_name for resource in page.resources)


def test_the_resources_can_be_replaced(user_type, scim_provider):
    """Resources assigned to a storage can be read and searched, and the former ones are gone."""
    storage = InMemoryStorage()
    User = scim_provider.model_for(user_type)
    former = storage.create(user_type, User(user_name="bjensen"))
    kept = InMemoryStorage().create(user_type, User(user_name="jsmith"))
    assert found_user_names(storage, user_type, User, 'userName eq "bjensen"') == [
        "bjensen"
    ]

    storage.resources = [kept]

    assert storage.get(user_type, kept.id).user_name == "jsmith"
    with pytest.raises(NotFoundException):
        storage.get(user_type, former.id)
    assert found_user_names(storage, user_type, User, 'userName eq "bjensen"') == []
    assert found_user_names(storage, user_type, User, 'userName eq "jsmith"') == [
        "jsmith"
    ]
    storage.create(user_type, User(user_name="bjensen"))
    with pytest.raises(UniquenessException):
        storage.create(user_type, User(user_name="jsmith"))


def test_an_equality_search_follows_the_writes(user_type, scim_provider):
    """A search for an equal value finds the resources as they are after each write."""
    storage = InMemoryStorage()
    User = scim_provider.model_for(user_type)
    user = storage.create(user_type, User(user_name="bjensen", external_id="a"))
    assert found_user_names(storage, user_type, User, 'externalId eq "a"') == [
        "bjensen"
    ]

    user.external_id = "b"
    storage.update(user_type, user)
    assert found_user_names(storage, user_type, User, 'externalId eq "a"') == []
    assert found_user_names(storage, user_type, User, 'externalId eq "b"') == [
        "bjensen"
    ]

    storage.create(user_type, User(user_name="jsmith", external_id="b"))
    assert found_user_names(storage, user_type, User, 'externalId eq "b"') == [
        "bjensen",
        "jsmith",
    ]

    storage.delete(user_type, user.id)
    assert found_user_names(storage, user_type, User, 'externalId eq "b"') == ["jsmith"]


def test_an_equality_search_follows_the_case_exactness(user_type, scim_provider):
    """A search for an equal value ignores the case unless the attribute is case-exact."""
    storage = InMemoryStorage()
    User = scim_provider.model_for(user_type)
    storage.create(user_type, User(user_name="bjensen", external_id="a"))

    assert found_user_names(storage, user_type, User, 'userName eq "BJensen"') == [
        "bjensen"
    ]
    assert found_user_names(storage, user_type, User, 'externalId eq "A"') == []


def test_an_equality_search_for_a_value_the_policy_cannot_compare(
    user_type, scim_provider
):
    """A search for a value the policy refuses finds nothing."""
    storage = InMemoryStorage()
    User = scim_provider.model_for(user_type)
    storage.create(user_type, User(user_name="bjensen!"))

    with ScimPolicy(comparison_key=refuse_exclamation_marks):
        assert (
            found_user_names(storage, user_type, User, 'userName eq "bjensen!"') == []
        )


def test_an_equality_search_skips_the_stored_values_the_policy_cannot_compare(
    user_type, scim_provider
):
    """A stored value the policy refuses matches nothing, and the search finds the other resources."""
    storage = InMemoryStorage()
    User = scim_provider.model_for(user_type)
    storage.create(user_type, User(user_name="bjensen!"))
    storage.create(user_type, User(user_name="jsmith"))

    with ScimPolicy(comparison_key=refuse_exclamation_marks):
        assert found_user_names(storage, user_type, User, 'userName eq "JSmith"') == [
            "jsmith"
        ]


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


def test_uniqueness_compares_the_unicode_forms_of_a_value(user_type, scim_provider):
    """A userName written with a combining accent clashes with the same userName written with a precomposed one."""
    storage = InMemoryStorage()
    User = scim_provider.model_for(user_type)
    storage.create(user_type, User(user_name="Jos\u00e9"))
    with pytest.raises(UniquenessException):
        storage.create(user_type, User(user_name="JOSE\u0301"))


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


def test_unique_values_ignore_the_case_of_non_ascii_letters(user_type, scim_provider):
    """The case of non-ASCII letters is ignored: "élise" and "ÉLISE" clash."""
    storage = InMemoryStorage()
    User = scim_provider.model_for(user_type)
    storage.create(user_type, User(user_name="élise"))
    with pytest.raises(UniquenessException):
        storage.create(user_type, User(user_name="ÉLISE"))


def refuse_exclamation_marks(binding, value):
    if "!" in value:
        raise ValueError("exclamation mark")
    return default_comparison_key(binding, value)


def test_a_value_the_policy_cannot_compare_never_clashes(user_type, scim_provider):
    """A stored value the policy refuses is equal to no other value, and does not fail the next writes."""
    storage = InMemoryStorage()
    User = scim_provider.model_for(user_type)
    storage.create(user_type, User(user_name="bjensen!"))

    with ScimPolicy(comparison_key=refuse_exclamation_marks):
        storage.create(user_type, User(user_name="jsmith"))
        storage.create(user_type, User(user_name="bjensen!"))

    assert len(storage.resources) == 3


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


def test_hidden_unique_values_are_not_checked():
    """A unique value clients never read can be held twice: refusing it would tell that another resource holds it."""

    class Lock(ComplexAttribute):
        code: Annotated[str | None, Uniqueness.server] = None

    class Key(ComplexAttribute):
        code: Annotated[str | None, Mutability.write_only, Uniqueness.server] = None

    class Safe(Resource):
        __schema__ = URN("urn:example:2.0:Safe")
        pin: Annotated[str | None, Returned.never, Uniqueness.server] = None
        lock: Annotated[Lock | None, Returned.never] = None
        key: Key | None = None

    storage = InMemoryStorage()
    safe_type = ResourceType.from_resource(Safe)
    for _ in range(2):
        storage.create(
            safe_type, Safe(pin="1234", lock=Lock(code="a"), key=Key(code="b"))
        )


def test_loaded_resources_sharing_a_unique_value_can_be_deleted():
    """Resources loaded with a shared unique value are deleted one after the other, and free it."""
    mailbox_type = ResourceType.from_resource(Mailbox)
    first, second = (
        Mailbox(
            id=mailbox_id,
            aliases=["babs"],
            meta=Meta(resource_type=mailbox_type.name, version='W/"1"'),
        )
        for mailbox_id in ("1", "2")
    )
    storage = InMemoryStorage()
    storage.resources = [first, second]

    storage.delete(mailbox_type, "1")
    storage.delete(mailbox_type, "2")

    storage.create(mailbox_type, Mailbox(aliases=["babs"]))


def test_complex_unique_values_clash_when_equal():
    """Equal complex values of a unique attribute clash, and so do equal entries of a unique multi-valued complex attribute."""

    class Size(ComplexAttribute):
        width: int | None = None

    class Badge(Resource):
        __schema__ = URN("urn:example:2.0:Badge")
        size: Annotated[Size | None, Uniqueness.server] = None
        sizes: Annotated[list[Size] | None, Uniqueness.server] = None

    storage = InMemoryStorage()
    badge_type = ResourceType.from_resource(Badge)
    storage.create(badge_type, Badge(size=Size(width=1), sizes=[Size(width=1)]))
    storage.create(badge_type, Badge(size=Size(width=2), sizes=[Size(width=2)]))
    with pytest.raises(UniquenessException):
        storage.create(badge_type, Badge(size=Size(width=2)))
    with pytest.raises(UniquenessException):
        storage.create(badge_type, Badge(sizes=[Size(width=3), Size(width=1)]))


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

    page = storage.search(resource_types, SearchRequest(filter='userName eq "bob"'))

    assert page.total == 1
    assert page.resources[0].user_name == "bob"


def test_an_unbound_filter_without_resource(user_type):
    """With no stored resource, an unbound filter has nothing to be resolved against nor to match."""
    storage = InMemoryStorage()
    assert storage.search(
        [user_type], SearchRequest(filter='userName eq "bob"')
    ) == SearchPage(0, [])


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
    assert storage.search([resource_type], SearchRequest()).total == 1
    with pytest.raises(UniquenessException):
        storage.create(resource_type, User(user_name="bjensen"))
