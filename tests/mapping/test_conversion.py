import pytest
from scim2_models import InvalidValueException
from scim2_models import MutabilityException
from scim2_models import SearchRequest

from scim2_server.mapping import Attribute
from scim2_server.mapping import Entry
from scim2_server.mapping import Many
from scim2_server.utils import load_default_provider

from .backend import FakeStorage
from .backend import Owned
from .pets import tags
from .policy import REFUSING
from .tables import UNDATED
from .tables import WORK_EMAIL
from .tables import emails
from .tables import groups_mapping
from .tables import users
from .tables import users_mapping


@pytest.fixture
def provider():
    return load_default_provider()


@pytest.fixture
def user_type(provider):
    return next(rt for rt in provider.resource_types if rt.id == "User")


@pytest.fixture
def group_type(provider):
    return next(rt for rt in provider.resource_types if rt.id == "Group")


@pytest.fixture
def User(provider, user_type):
    return provider.model_for(user_type)


@pytest.fixture
def Group(provider, group_type):
    return provider.model_for(group_type)


def storage_of(provider, changes=None, **options):
    mappings = {"User": users_mapping(changes, **options), "Group": groups_mapping()}
    return FakeStorage(mappings, provider)


def test_a_resource_without_counter_needs_a_version_key(provider, user_type, User):
    """Without a counter, the version is a hash, which needs a key."""
    storage = storage_of(provider, UNDATED, version=None)

    with pytest.raises(ValueError, match="has no counter, and needs a version key"):
        storage.create(user_type, User(user_name="alice"))


def test_a_read_only_value_cannot_change(provider, user_type, User):
    """A client cannot change the nickname the storage only reads."""
    nick_name = Attribute(users.fields["nick_name"], writable=False)
    storage = storage_of(provider, {"nickName": nick_name})
    alice = storage.create(user_type, User(user_name="alice"))

    with pytest.raises(MutabilityException, match="nickName' is read-only"):
        storage.update(user_type, User(id=alice.id, user_name="alice", nick_name="al"))


def test_a_read_only_value_left_as_it_is_is_accepted(provider, user_type, User):
    """A client may send back the read-only nickname it read."""
    nick_name = Attribute(users.fields["nick_name"], writable=False)
    storage = storage_of(provider, {"nickName": nick_name})
    alice = storage.create(user_type, User(user_name="alice"))
    users.rows[int(alice.id)]["nick_name"] = "al"

    updated = storage.update(
        user_type, User(id=alice.id, user_name="alice", nick_name="al")
    )

    assert updated.nick_name == "al"


def test_a_value_the_mapping_does_not_store_is_refused(provider, user_type, User):
    """A title would be lost, so it is refused rather than ignored."""
    storage = storage_of(provider, {"title": None})

    with pytest.raises(InvalidValueException, match="title' is not stored"):
        storage.create(user_type, User(user_name="alice", title="boss"))


def test_a_value_whose_field_needs_one_is_required(provider, user_type, User):
    """A new record cannot be created without a value for a field without null."""
    storage = storage_of(provider, {"title": users.fields["user_name"]})

    with pytest.raises(InvalidValueException, match="title' is required"):
        storage.create(user_type, User(user_name="alice"))


def test_a_value_whose_field_has_no_null_cannot_be_removed(provider, user_type, User):
    """The field has a default but no null, so the title cannot be emptied."""
    storage = storage_of(provider, {"title": users.fields["code"]})
    alice = storage.create(user_type, User(user_name="alice", title="boss"))

    with pytest.raises(MutabilityException, match="title' cannot be removed"):
        storage.update(user_type, User(id=alice.id, user_name="alice"))


def test_an_entry_needs_the_values_its_fields_need(provider, user_type, User):
    """An email without address would fail in the database."""
    many = Many(
        Owned(emails, "user"),
        {"value": emails.fields["address"], "type": emails.fields["type"]},
    )
    storage = storage_of(provider, {"emails": many})

    with pytest.raises(InvalidValueException, match="emails.value' is required"):
        storage.create(user_type, User(user_name="alice", emails=[{"type": "work"}]))


def test_an_entry_holds_one_entry_at_most(provider, user_type, User):
    """The record has room for one email."""
    storage = storage_of(provider, WORK_EMAIL)
    two = [{"value": "a@example.com"}, {"value": "b@example.com"}]

    with pytest.raises(InvalidValueException, match="holds one entry at most"):
        storage.create(user_type, User(user_name="alice", emails=two))


def test_an_entry_holds_its_constants(provider, user_type, User):
    """The email of the record is always the work one."""
    storage = storage_of(provider, WORK_EMAIL)
    home = [{"value": "a@example.com", "type": "home"}]

    with pytest.raises(InvalidValueException, match="emails.type' can only be 'work'"):
        storage.create(user_type, User(user_name="alice", emails=home))


def test_an_entry_takes_its_constants_in_any_case(provider, user_type, User):
    """The type is compared without its case, and any primary is read back as true."""
    storage = storage_of(provider, WORK_EMAIL)
    work = [{"value": "a@example.com", "type": "WORK", "primary": False}]

    alice = storage.create(user_type, User(user_name="alice", emails=work))

    assert [(e.type, e.primary) for e in alice.emails] == [("work", True)]


def test_a_value_out_of_the_choices_of_its_field_is_refused(provider, user_type, User):
    """The resources of the server are of the model of the provider, not of the mapping."""
    storage = storage_of(provider)
    pager = [{"value": "a@example.com", "type": "pager"}]

    with pytest.raises(InvalidValueException, match="cannot hold 'pager'"):
        storage.create(user_type, User(user_name="alice", emails=pager))


def test_written_entries_replace_the_former_ones(provider, user_type, User):
    """The former email records are deleted."""
    storage = storage_of(provider)
    alice = storage.create(
        user_type, User(user_name="alice", emails=[{"value": "a@example.com"}])
    )

    updated = storage.update(
        user_type,
        User(id=alice.id, user_name="alice", emails=[{"value": "b@example.com"}]),
    )

    assert [e.value for e in updated.emails] == ["b@example.com"]
    assert len(emails.rows) == 1


@pytest.fixture
def people(provider, user_type, User):
    storage = storage_of(provider)
    for name in ("alice", "bob", "carol"):
        storage.create(user_type, User(user_name=name))
    return storage


def test_a_link_entry_needs_a_value(people, group_type, Group):
    """A member with a display only cannot be stored."""
    with pytest.raises(
        InvalidValueException, match="members' holds an entry without value"
    ):
        people.create(group_type, Group(display_name="x", members=[{"display": "x"}]))


def test_a_link_to_an_unknown_resource_is_refused(people, group_type, Group):
    """No user nor group has this identifier."""
    with pytest.raises(InvalidValueException, match="links to an unknown resource"):
        people.create(group_type, Group(display_name="x", members=[{"value": "9"}]))


def test_a_link_entry_links_to_a_resource_of_its_type(people, group_type, Group):
    """Carol is a user, and no group has her identifier."""
    member = {"value": "3", "type": "Group"}

    with pytest.raises(
        InvalidValueException, match="links to '3', which is not of type 'Group'"
    ):
        people.create(group_type, Group(display_name="x", members=[member]))


def test_a_link_entry_with_a_type_the_policy_cannot_prepare_links_to_nothing(
    people, group_type, Group
):
    """The type cannot be compared, so it matches no Link."""
    people.policy = REFUSING
    member = {"value": "3", "type": "User!"}

    with pytest.raises(InvalidValueException, match="which is not of type 'User!'"):
        people.create(group_type, Group(display_name="x", members=[member]))


def test_a_resource_linked_twice_is_linked_once(people, group_type, Group):
    """A member sent twice is stored once."""
    member = {"value": "3", "type": "User"}

    group = people.create(group_type, Group(display_name="x", members=[member, member]))

    assert [m.value for m in group.members] == ["3"]


def test_an_entry_of_a_simple_attribute_without_value_is_not_read(pets_storage):
    """A tag row without value, written by another application, is left out."""
    storage, pet_type = pets_storage
    tags.rows[99] = {"id": 99, "pet": 3, "value": None, "label": None}

    kit = storage.get(pet_type, "3")

    assert kit.tags is None


def test_a_string_compares_without_case_unless_case_exact():
    """The externalId is case exact, and active is not a string."""
    mapping = users_mapping()

    assert mapping._column("userName").casefolded
    assert not mapping._column("externalId").casefolded
    assert not mapping._column("active").casefolded


def test_the_string_columns_hold_the_strings_of_the_record_and_of_the_entries():
    """A SQL key function needs the strings of the record and of the entries."""
    urns = {
        column.binding.urn.rsplit(":", 1)[-1]
        for column in users_mapping()._string_columns()
    }

    assert "User:userName" in urns or "userName" in urns
    assert "emails.value" in urns
    assert "active" not in urns
    assert "emails.primary" not in urns


def test_a_constant_is_found_among_several_entries(provider, user_type, User):
    """The type of the phone number is the one of its Entry, not of the email one."""
    phone = Entry({"value": users.fields["phone"]}, type="work")
    storage = storage_of(provider, WORK_EMAIL | {"phoneNumbers": phone})
    storage.create(user_type, User(user_name="alice", phone_numbers=[{"value": "1"}]))
    storage.create(user_type, User(user_name="bob"))

    page = storage.search(
        [user_type], SearchRequest(filter='phoneNumbers.type eq "work"')
    )

    assert [user.user_name for user in page.resources] == ["alice"]
