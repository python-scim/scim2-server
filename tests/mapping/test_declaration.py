import re

import pytest
from scim2_models import User

from scim2_server.mapping import Attribute
from scim2_server.mapping import Entry
from scim2_server.mapping import Link
from scim2_server.mapping import Many

from .backend import FakeMapping
from .backend import Owned
from .backend import Reference
from .backend import References
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

TITLE = users.fields["title"]
EMAILS = Owned(emails, "user")
MANAGER = Reference(users.fields["manager"], users)


def refused(message, changes, mapping=users_mapping):
    with pytest.raises(ValueError, match=re.escape(message)):
        mapping(changes=changes, resolve=False)


def test_a_mapping_shows_its_model():
    """The representation names the model of the resources."""
    assert repr(users_mapping()) == "<FakeMapping of User[EnterpriseUser]>"


def test_two_paths_to_the_same_attribute_are_refused():
    """Paths ignore the case, so both map userName."""
    refused("'userName' and 'username' map the same attribute", {"username": TITLE})


def test_a_path_and_one_of_its_sub_attributes_are_refused():
    """name.givenName is already mapped, so name cannot be."""
    refused("'name.givenName' and 'name' map the same attribute", {"name": TITLE})


def test_a_mapping_needs_the_identifier():
    """A resource cannot be found again without its id."""
    refused("The mapping of User[EnterpriseUser] needs 'id'", {"id": None})


def test_a_path_out_of_the_model_is_refused():
    """A typo in a path raises when the mapping is created."""
    refused("'nickname.first' is not an attribute of", {"nickname.first": TITLE})


def test_a_link_needs_a_complex_attribute():
    """A title holds no value nor $ref."""
    refused("'title' is not a complex attribute", {"title": Link(MANAGER, "User")})


def test_a_single_link_holds_one_resource_type():
    """The manager holds one value, so it links to one resource type."""
    links = [Link(MANAGER, "User"), Link(MANAGER, "Group")]
    refused(
        "holds one value, so it is mapped to one Link", {f"{ENTERPRISE}:manager": links}
    )


def test_a_complex_attribute_needs_many_link_or_its_sub_attributes():
    """One target cannot hold the whole name."""
    changes = {"name.givenName": None, "name.familyName": None, "name": TITLE}
    refused("'name' holds several values or sub-attributes", changes)


def test_a_multi_valued_attribute_needs_many_or_link():
    """One target cannot hold several emails."""
    refused("'emails' holds several values or sub-attributes", {"emails": TITLE})


def test_a_value_the_backend_cannot_map_is_refused():
    """An integer is not a field of the backend."""
    refused("'title' cannot be mapped to 42", {"title": 42})


def test_an_attribute_clients_neither_read_nor_write_is_refused():
    """The password is write-only, so a read-only one would be useless."""
    password = Attribute(users.fields["password"], writable=False)
    refused("Clients could neither read nor write 'password'", {"password": password})


def test_a_multi_valued_sub_attribute_of_a_complex_attribute_is_refused():
    """One field cannot hold the aliases of an owner."""
    changes = {"owner": None, "owner.aliases": pets.fields["name"]}
    refused(
        "'owner.aliases' holds several values in a complex attribute",
        changes,
        pets_mapping,
    )


def test_many_needs_a_multi_valued_attribute():
    """A title holds a single value."""
    refused(
        "'title' is not a multi-valued simple attribute", {"title": Many(EMAILS, TITLE)}
    )


def test_many_of_one_target_needs_a_simple_attribute():
    """Emails have sub-attributes, so each needs its own target."""
    refused(
        "'emails' is not a multi-valued simple attribute",
        {"emails": Many(EMAILS, emails.fields["value"])},
    )


def test_many_of_sub_attributes_needs_a_complex_attribute():
    """Tags are strings, which have no value sub-attribute."""
    refused(
        "'tags' is not a multi-valued complex attribute",
        {"tags": Many(Owned(tags, "pet"), {"value": tags.fields["value"]})},
        pets_mapping,
    )


def test_many_maps_a_simple_attribute_to_a_target():
    """An integer is not a field of the backend."""
    refused(
        "'tags' cannot be mapped to 42",
        {"tags": Many(Owned(tags, "pet"), 42)},
        pets_mapping,
    )


def test_many_refuses_two_paths_to_the_same_sub_attribute():
    """Paths ignore the case, so both map emails.value."""
    many = Many(
        EMAILS, {"value": emails.fields["value"], "VALUE": emails.fields["type"]}
    )
    refused(
        "'emails.value' and 'emails.VALUE' map the same attribute", {"emails": many}
    )


def test_many_refuses_a_path_out_of_the_attribute():
    """Emails have no nope sub-attribute."""
    many = Many(EMAILS, {"nope": emails.fields["value"]})
    refused("'emails.nope' is not an attribute of", {"emails": many})


def test_many_maps_each_sub_attribute_to_a_target():
    """An integer is not a field of the backend."""
    refused(
        "'emails.value' cannot be mapped to 42", {"emails": Many(EMAILS, {"value": 42})}
    )


def test_entry_needs_a_multi_valued_complex_attribute():
    """A name holds no list of entries."""
    changes = {"name.givenName": None, "name.familyName": None}
    changes["name"] = Entry({"givenName": TITLE})
    refused("'name' is not a multi-valued complex attribute", changes)


def test_entry_needs_a_sub_attribute():
    """An entry without field would never exist."""
    refused("The Entry of 'emails' maps no sub-attribute", {"emails": Entry({})})


def test_entry_maps_each_sub_attribute_to_a_target():
    """An integer is not a field of the backend."""
    refused("'emails.value' cannot be mapped to 42", {"emails": Entry({"value": 42})})


def test_entry_cannot_give_a_sub_attribute_it_maps():
    """The type cannot be both a field and a constant."""
    entry = Entry({"type": users.fields["email"]}, type="work")
    refused(
        "The Entry of 'emails' gives 'type', which is not a sub-attribute it leaves unmapped",
        {"emails": entry},
    )


def test_entry_cannot_give_a_sub_attribute_the_attribute_lacks():
    """Secrets have no type sub-attribute."""
    entry = Entry({"value": secrets.fields["value"]}, type="work")
    refused("The Entry of 'secrets' gives 'type'", {"secrets": entry}, pets_mapping)


def test_a_multi_valued_link_needs_a_link():
    """An empty list of Links maps nothing."""
    refused(
        "'groups' needs one Link per resource type, and at least one", {"groups": []}
    )


def test_links_need_distinct_resource_types():
    """The storage could not tell which Link a member belongs to."""
    links = [Link(References(groups.fields["members"], users), "User")] * 2
    refused(
        "'members' needs one Link per resource type", {"members": links}, groups_mapping
    )


def test_links_all_have_a_type_or_none():
    """A member without type could not be told apart."""
    links = [
        Link(References(groups.fields["members"], users), "User", type="User"),
        Link(References(groups.fields["nested"], groups), "Group"),
    ]
    refused(
        "Either every Link of 'members' has a type, or none",
        {"members": links},
        groups_mapping,
    )


def test_a_typed_link_needs_a_type_sub_attribute():
    """The manager has no type sub-attribute to hold the type."""
    link = Link(MANAGER, "User", type="boss")
    refused(
        f"'{ENTERPRISE}:manager' has no type sub-attribute",
        {f"{ENTERPRISE}:manager": link},
    )


def test_an_attribute_needs_a_target_of_the_backend():
    """A string is not a field of this backend."""
    refused("'title' cannot be mapped to", {"title": Attribute("title")})


def test_the_backend_checks_each_value_with_its_holder():
    """The backend gets the values of the record, of a Many and of an Entry."""
    checked = []

    class Checking(FakeMapping):
        def _check_declared(self, path, binding, value, holder):
            checked.append((path, type(holder).__name__))

    many = Many(Owned(tags, "pet"), tags.fields["value"])
    entry = Entry({"value": pets.fields["code"]})
    Checking(
        Pet,
        pets,
        {"id": pets.id, "name": pets.fields["name"], "tags": many, "secrets": entry},
        resolve=False,
    )
    Checking(
        User,
        users,
        {"id": users.id, "emails": Many(EMAILS, {"value": TITLE})},
        resolve=False,
    )

    assert checked == [
        ("id", "NoneType"),
        ("name", "NoneType"),
        ("tags", "Many"),
        ("secrets.value", "Entry"),
        ("id", "NoneType"),
        ("emails.value", "Many"),
    ]
