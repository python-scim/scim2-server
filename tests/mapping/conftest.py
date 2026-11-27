import pytest
from scim2_models import ScimProvider

from scim2_server.mapping import Link
from scim2_server.utils import load_default_provider

from .backend import FakeStorage
from .backend import Reference
from .pets import PET_TABLES
from .pets import Pet
from .pets import pets
from .pets import pets_mapping
from .tables import TABLES
from .tables import groups_mapping
from .tables import users_mapping


@pytest.fixture(autouse=True)
def empty_tables():
    for table in (*TABLES, *PET_TABLES):
        table.rows.clear()
        table.next_key = 1


@pytest.fixture
def users_storage():
    provider = load_default_provider()
    storage = FakeStorage(
        {"User": users_mapping(), "Group": groups_mapping()}, provider
    )
    user_type, group_type = (
        next(rt for rt in provider.resource_types if rt.id == name)
        for name in ("User", "Group")
    )
    model = provider.model_for(user_type)
    alice = storage.create(user_type, model(user_name="alice"))
    bob = storage.create(user_type, model(user_name="bob"))
    group_model = provider.model_for(group_type)
    admins = storage.create(
        group_type,
        group_model(
            display_name="admins", members=[{"value": alice.id, "type": "User"}]
        ),
    )
    storage.create(
        group_type,
        group_model(
            display_name="staff",
            members=[{"value": bob.id}, {"value": admins.id, "type": "Group"}],
        ),
    )
    return storage, user_type, group_type


@pytest.fixture
def pets_storage():
    provider = ScimProvider(models=[Pet])
    owner = Link(Reference(pets.fields["parent"], pets), "Pet")
    mappings = {"Pet": pets_mapping(changes={"owner": owner})}
    storage = FakeStorage(mappings, provider, version_key=b"secret")
    (pet_type,) = provider.resource_types
    rex = storage.create(pet_type, Pet(name="rex", tags=["Big", "brown"]))
    storage.create(pet_type, Pet(name="tom", tags=["small"], owner={"value": rex.id}))
    storage.create(pet_type, Pet(name="kit"))
    return storage, pet_type
