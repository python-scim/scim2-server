from scim2_models import Mutability
from scim2_models import Required
from scim2_models import Returned

from scim2_server.mapping import Attribute
from scim2_server.mapping import Entry
from scim2_server.mapping import Many
from scim2_server.utils import load_default_provider

from .backend import FakeStorage
from .backend import Owned
from .pets import pets
from .pets import pets_mapping
from .pets import tags
from .tables import ENTERPRISE
from .tables import groups_mapping
from .tables import users
from .tables import users_mapping

WORK_EMAIL = Entry({"value": users.fields["email"]}, type="work", primary=True)


def schema(mapping, urn=None):
    """Return the attributes of a schema of a mapping bound to its links, by name."""
    mappings = {"User": users_mapping(), "Group": groups_mapping()}
    mappings[mapping.model.__name__.split("[")[0]] = mapping
    FakeStorage(mappings, load_default_provider())
    schemas = {schema.id: schema for schema in mapping.schemas()}
    found = schemas[urn or next(iter(schemas))]
    return {attribute.name: attribute for attribute in found.attributes}


def subs(attribute):
    return {sub.name: sub for sub in attribute.sub_attributes}


def test_the_schemas_hold_the_core_schema_and_the_extensions():
    """The enterprise extension comes after the user schema."""
    mapping = users_mapping()
    FakeStorage({"User": mapping, "Group": groups_mapping()}, load_default_provider())

    assert [schema.id for schema in mapping.schemas()] == [
        "urn:ietf:params:scim:schemas:core:2.0:User",
        ENTERPRISE,
    ]


def test_the_schemas_leave_out_the_attributes_the_mapping_does_not_store():
    """Clients only see what the server stores."""
    attributes = schema(users_mapping({"nickName": None, "name.familyName": None}))

    assert "nickName" not in attributes
    assert "addresses" not in attributes
    assert list(subs(attributes["name"])) == ["givenName"]


def test_an_attribute_whose_field_needs_a_value_is_required():
    """A new user needs a title, held by a field without null."""
    attributes = schema(users_mapping({"title": users.fields["user_name"]}))

    assert attributes["title"].required == Required.true


def test_a_complex_attribute_whose_sub_attribute_needs_a_value_is_required():
    """A new user needs a name, since its given name needs a value."""
    attributes = schema(users_mapping({"name.givenName": users.fields["user_name"]}))

    assert attributes["name"].required == Required.true


def test_a_multi_valued_attribute_whose_entries_need_a_value_is_not_required():
    """A pet may have no tag, even if each tag needs a label."""
    many = Many(Owned(tags, "pet"), tags.fields["label"])
    attributes = {
        s.name: s for s in pets_mapping(changes={"tags": many}).schemas()[0].attributes
    }

    assert attributes["tags"].required == Required.false


def test_a_hidden_attribute_is_never_required():
    """Clients cannot send back a password they never read."""
    attributes = schema(users_mapping({"password": users.fields["user_name"]}))

    assert attributes["password"].required == Required.false


def test_an_attribute_the_storage_does_not_write_is_read_only():
    """Clients know the nickname cannot change."""
    nick_name = Attribute(users.fields["nick_name"], writable=False)

    attributes = schema(users_mapping({"nickName": nick_name}))

    assert attributes["nickName"].mutability == Mutability.read_only


def test_the_constants_of_an_entry_are_its_canonical_values():
    """The type of the email can only be work."""
    emails = subs(schema(users_mapping({"emails": WORK_EMAIL}))["emails"])

    assert emails["type"].canonical_values == ["work"]
    assert emails["type"].mutability == Mutability.read_write
    assert emails["primary"].canonical_values is None


def test_the_constants_of_a_read_only_entry_are_read_only():
    """The storage never writes the email, nor its type."""
    entry = Entry(
        {"value": Attribute(users.fields["email"], writable=False)}, type="work"
    )

    emails = subs(schema(users_mapping({"emails": entry}))["emails"])

    assert emails["type"].mutability == Mutability.read_only
    assert emails["value"].mutability == Mutability.read_only


def test_a_link_refers_to_the_resource_types_of_its_links():
    """Members are users or groups, and show a display."""
    members = subs(schema(groups_mapping())["members"])

    assert members["$ref"].reference_types == ["User", "Group"]
    assert members["type"].canonical_values == ["User", "Group"]
    assert members["display"].mutability == Mutability.read_only


def test_a_link_of_an_extension_refers_to_its_resource_type():
    """The manager is a user."""
    manager = subs(schema(users_mapping(), ENTERPRISE)["manager"])

    assert manager["$ref"].reference_types == ["User"]
    assert manager["displayName"].mutability == Mutability.read_only


def test_a_complex_attribute_of_read_only_values_is_read_only():
    """The storage only reads the groups of a user."""
    assert schema(users_mapping())["groups"].mutability == Mutability.read_only


def test_a_complex_attribute_of_hidden_values_is_hidden_and_write_only():
    """Clients never read the code of the vault."""
    vault = {
        s.name: s
        for s in pets_mapping(changes={"vault.code": pets.fields["code"]})
        .schemas()[0]
        .attributes
    }["vault"]

    assert vault.returned == Returned.never
    assert vault.mutability == Mutability.write_only


def test_a_complex_attribute_of_hidden_values_keeps_a_mutability_other_than_read_write():
    """The safe stays immutable."""
    safe = {
        s.name: s
        for s in pets_mapping(changes={"safe.code": pets.fields["code"]})
        .schemas()[0]
        .attributes
    }["safe"]

    assert safe.returned == Returned.never
    assert safe.mutability == Mutability.immutable
