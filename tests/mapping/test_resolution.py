import re
from typing import Annotated

import pytest
from scim2_models import Required

from scim2_server.mapping import Attribute
from scim2_server.mapping import Entry
from scim2_server.mapping import Link
from scim2_server.mapping import Many
from scim2_server.mapping import ResourceMapping
from scim2_server.utils import load_default_provider

from .backend import FakeStorage
from .backend import Owned
from .backend import Reference
from .backend import References
from .pets import Keeper
from .pets import Pet
from .pets import pets
from .pets import pets_mapping
from .pets import secrets
from .pets import tags
from .tables import ENTERPRISE
from .tables import emails
from .tables import groups
from .tables import groups_mapping
from .tables import users
from .tables import users_mapping

EMAILS = Owned(emails, "user")
EMAIL = users.fields["email"]


def problems(mapping, changes, **options):
    resolved = mapping(changes=changes, resolve=False, **options)._resolve()
    return [(problem.level, problem.code) for problem in resolved]


def collection(mapping, name):
    (found,) = [c for c in mapping._all_collections() if c.binding.field_name == name]
    return found


def storage(**changes):
    mappings = {
        "User": users_mapping(changes.get("User")),
        "Group": groups_mapping(changes.get("Group")),
    }
    return FakeStorage(mappings, load_default_provider())


def test_the_problems_of_a_mapping_are_built_once():
    """Resolving twice gives the same list."""
    mapping = users_mapping(resolve=False)

    assert mapping._resolve() is mapping._resolve()


def test_a_mapping_without_problem_resolves():
    """The users mapping has no error nor warning."""
    assert users_mapping(resolve=False)._resolve() == []


def test_the_errors_stop_the_creation_of_a_mapping():
    """The test backend raises the errors when it creates a mapping."""
    with pytest.raises(ValueError, match="'title' is not mapped to a field of users"):
        users_mapping({"title": emails.fields["value"]})


def test_an_error_of_the_backend_is_a_problem():
    """The title is mapped to a field of the emails."""
    assert problems(users_mapping, {"title": emails.fields["value"]}) == [
        ("error", "unknown-field")
    ]


def test_an_identifier_the_backend_refuses_stops_the_resolution():
    """Without id, the checks of the record cannot run."""
    assert problems(users_mapping, {"id": emails.id}) == [("error", "unknown-field")]


def test_a_problem_of_many_is_reported():
    """The relation of the emails is not one the backend accepts."""
    many = Many(users.id, {"value": emails.fields["value"]})

    assert problems(users_mapping, {"emails": many}) == [("error", "relation-kind")]


def test_a_problem_of_a_simple_many_is_reported():
    """The tags are mapped to a field of the pets."""
    changes = {"tags": Many(Owned(tags, "pet"), pets.fields["name"])}

    assert problems(pets_mapping, changes) == [("error", "unknown-field")]


def test_a_problem_of_a_sub_attribute_of_many_is_reported():
    """The value of the emails is mapped to a field of the users."""
    changes = {"emails": Many(EMAILS, {"value": users.fields["title"]})}

    assert problems(users_mapping, changes) == [("error", "unknown-field")]


def test_entries_cannot_hide_their_values():
    """Each write replaces the entries, and clients never send back the secrets."""
    many = Many(Owned(secrets, "pet"), {"value": secrets.fields["value"]})

    assert problems(pets_mapping, {"secrets": many}) == [("error", "hidden-in-entries")]


def test_written_entries_cannot_hold_a_read_only_value():
    """Each write would lose the read-only type."""
    many = Many(
        EMAILS,
        {
            "value": emails.fields["value"],
            "type": Attribute(emails.fields["type"], writable=False),
        },
    )

    assert problems(users_mapping, {"emails": many}) == [
        ("error", "read-only-in-entries")
    ]


def test_entries_of_read_only_values_are_never_written():
    """Emails the storage only reads are left as they are."""
    many = Many(EMAILS, {"value": Attribute(emails.fields["value"], writable=False)})

    assert not collection(users_mapping({"emails": many}), "emails").writable


def test_a_problem_of_a_sub_attribute_of_an_entry_is_reported():
    """The email is mapped to a field of the emails table."""
    entry = Entry({"value": emails.fields["value"]})

    assert problems(users_mapping, {"emails": entry}) == [("error", "unknown-field")]


def test_the_fields_of_an_entry_need_null():
    """Removing the entry empties its fields."""
    entry = Entry({"value": EMAIL, "display": users.fields["user_name"]})

    assert problems(users_mapping, {"emails": entry}) == [("error", "entry-fields")]


def test_the_fields_of_an_entry_are_read():
    """The entry exists when a field has a value, which clients must read."""
    entry = Entry({"value": pets.fields["name"]})

    assert problems(pets_mapping, {"secrets": entry}) == [("error", "entry-fields")]


def test_the_fields_of_an_entry_are_all_written_or_none():
    """Removing the entry would empty the read-only display."""
    entry = Entry(
        {"value": EMAIL, "display": Attribute(users.fields["title"], writable=False)}
    )

    assert problems(users_mapping, {"emails": entry}) == [("error", "entry-fields")]


def test_an_entry_of_read_only_values_is_never_written():
    """An email the storage only reads is left as it is."""
    mapping = users_mapping(
        {"emails": Entry({"value": Attribute(EMAIL, writable=False)})}
    )

    assert not mapping._entries[(mapping.model, "emails")].writable


def test_a_problem_of_a_link_is_reported():
    """The manager holds one value, so it needs a single reference."""
    link = Link(References(users.fields["manager"], users), "User")

    assert problems(users_mapping, {f"{ENTERPRISE}:manager": link}) == [
        ("error", "relation-kind")
    ]


def test_a_single_link_needs_null():
    """Removing the manager writes no value in its field."""
    link = Link(Reference(users.fields["user_name"], users), "User")

    assert problems(users_mapping, {f"{ENTERPRISE}:manager": link}) == [
        ("error", "single-link-null")
    ]


def test_a_required_single_link_needs_no_null():
    """Every pet has an owner, so its field needs no null."""
    assert problems(pets_mapping, {}) == []


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        (Pet[Annotated[Keeper, Required.true]], []),
        (Pet[Keeper], [("error", "single-link-null")]),
    ],
    ids=["required extension", "optional extension"],
)
def test_a_single_link_of_an_extension_needs_null_unless_the_extension_is_required(
    model, expected
):
    """A pet without the extension has no keeper."""
    keeper = Link(Reference(pets.fields["keeper"], pets), "Pet")
    mapping = pets_mapping(
        model, {"urn:example:2.0:Keeper:keeper": keeper}, resolve=False
    )

    assert [(p.level, p.code) for p in mapping._resolve()] == expected


def test_a_field_needing_a_value_and_never_written_is_a_warning():
    """Creating a user would fail without its user name."""
    assert problems(users_mapping, {"userName": None}) == [("warning", "unwritten")]


def test_the_dates_are_written():
    """The storage writes the creation date itself."""
    changes = {"userName": None, "meta.created": users.fields["user_name"]}

    assert problems(users_mapping, changes) == []


def test_the_base_mapping_asks_no_field():
    """A backend gives the fields its records need."""
    assert list(ResourceMapping._fields_needing_value(users_mapping())) == []


def test_a_link_needs_the_mapping_of_its_resource_type():
    """The groups of the users have no mapping."""
    with pytest.raises(ValueError, match="No mapping for the resource type 'Group'"):
        FakeStorage({"User": users_mapping()}, load_default_provider())


def test_a_link_cannot_link_to_two_mappings_of_a_resource_type():
    """A mapping serves storages sharing the same mappings."""
    provider = load_default_provider()
    mapping = users_mapping()
    FakeStorage({"User": mapping, "Group": groups_mapping()}, provider)

    with pytest.raises(ValueError, match="already links to another mapping of 'Group'"):
        FakeStorage({"User": mapping, "Group": groups_mapping()}, provider)


def test_a_link_links_to_the_records_of_its_target():
    """The members are mapped to groups, but link to users."""
    links = [Link(References(groups.fields["members"], groups), "User")]

    with pytest.raises(
        ValueError,
        match=re.escape("members' links to other records than those of the mapping"),
    ):
        storage(Group={"members": links})


def test_the_entries_of_a_link_show_the_display_of_their_target():
    """Members show the display name of a user or of a group."""
    mapping = storage().mappings["Group"]
    members = [
        c for c in mapping._all_collections() if c.binding.field_name == "members"
    ]

    assert [sorted(c.columns) for c in members] == [["display", "value"]] * 2


def test_the_entries_of_a_link_lack_a_display_their_target_does_not_store():
    """Groups without display name give no display to the groups of a user."""
    groups_of_users = collection(
        storage(Group={"displayName": None}).mappings["User"], "groups"
    )

    assert sorted(groups_of_users.columns) == ["value"]


def test_a_resource_without_display_gives_none():
    """A pet has no display name nor user name."""
    assert pets_mapping()._display() is None
