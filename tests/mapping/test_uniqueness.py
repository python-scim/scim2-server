from typing import Annotated

import pytest
from scim2_models import ComplexAttribute
from scim2_models import Required
from scim2_models import Returned
from scim2_models import ScimProvider
from scim2_models import Uniqueness
from scim2_models import UniquenessException

from scim2_server.mapping import Attribute
from scim2_server.mapping import Entry
from scim2_server.mapping import Link
from scim2_server.testing import MAILBOX_TYPES
from scim2_server.testing import Mailbox
from scim2_server.testing import MailboxOwner

from .backend import FakeStorage
from .backend import Reference
from .pets import Pet
from .pets import pets
from .pets import pets_mapping
from .tables import MAILBOX_TABLES
from .tables import mailbox_mapping


class SoleOwner(ComplexAttribute):
    value: Annotated[str | None, Uniqueness.server] = None
    display: str | None = None


class Badge(ComplexAttribute):
    value: Annotated[str | None, Uniqueness.server] = None


class Puppy(Pet):
    name: Annotated[str | None, Uniqueness.server] = None
    tags: Annotated[list[str] | None, Uniqueness.server] = None
    owner: Annotated[SoleOwner | None, Required.true] = None
    badges: list[Badge] | None = None


class LockedVault(ComplexAttribute):
    code: Annotated[str | None, Returned.never, Uniqueness.server] = None


class LockedPet(Pet):
    vault: LockedVault | None = None


def puppies_mapping(changes=None, **options):
    attributes = {
        "owner": Link(Reference(pets.fields["parent"], pets), "Pet"),
        "badges": Entry({"value": pets.fields["code"]}),
    }
    return pets_mapping(Puppy, attributes | (changes or {}), **options)


@pytest.fixture
def puppies():
    provider = ScimProvider(models=[Puppy])
    storage = FakeStorage({"Pet": puppies_mapping()}, provider, version_key=b"key")
    (pet_type,) = provider.resource_types
    return storage, pet_type


def test_a_hidden_attribute_cannot_be_unique():
    """Refusing a hidden value would tell a client that another resource holds it."""
    mapping = pets_mapping(
        LockedPet, {"vault.code": pets.fields["code"]}, resolve=False
    )

    assert [(p.level, p.code) for p in mapping._resolve()] == [
        ("error", "hidden-unique")
    ]


def test_the_unique_attributes_are_those_clients_read():
    """A unique value is checked in the record, an Entry, the entries of a collection and a Link, even when the storage only reads it, but not in the id."""
    mapping = puppies_mapping({"name": Attribute(pets.fields["name"], writable=False)})
    FakeStorage({"Pet": mapping}, ScimProvider(models=[Puppy]))

    assert sorted(mapping._unique_attributes()) == [
        "urn:example:2.0:Pet:badges.value",
        "urn:example:2.0:Pet:name",
        "urn:example:2.0:Pet:owner.value",
        "urn:example:2.0:Pet:tags",
    ]


def test_a_value_another_mapping_only_reads_is_taken():
    """A unique value that the mapping of another resource type only reads clashes too."""
    shared_mailboxes = MAILBOX_TABLES["SharedMailbox"][0]
    owner = Attribute(shared_mailboxes.fields["owner"], writable=False)
    mappings = {
        "Mailbox": mailbox_mapping("Mailbox"),
        "SharedMailbox": mailbox_mapping("SharedMailbox", {"owner.value": owner}),
    }
    provider = ScimProvider(models=[Mailbox], resource_types=MAILBOX_TYPES)
    storage = FakeStorage(mappings, provider, version_key=b"key")
    shared_mailboxes.insert(shared_mailboxes.new() | {"owner": "bjensen"})

    with pytest.raises(UniquenessException):
        storage.create(MAILBOX_TYPES[0], Mailbox(owner=MailboxOwner(value="BJensen")))


def test_a_unique_link_value_is_taken_once(puppies):
    """No two puppies share an owner."""
    storage, pet_type = puppies
    rex = storage.create(pet_type, Puppy(name="rex"))
    storage.create(pet_type, Puppy(name="tom", owner={"value": rex.id}))

    with pytest.raises(UniquenessException):
        storage.create(pet_type, Puppy(name="kit", owner={"value": rex.id}))


def test_a_unique_value_of_an_entry_is_taken_once(puppies):
    """The badge of a puppy, held by its record, is not given to another one."""
    storage, pet_type = puppies
    rex = storage.create(pet_type, Puppy(name="rex", badges=[{"value": "gold"}]))
    storage.update(pet_type, rex)

    with pytest.raises(UniquenessException):
        storage.create(pet_type, Puppy(name="tom", badges=[{"value": "Gold"}]))
