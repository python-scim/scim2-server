"""A resource with what users and groups lack: a multi-valued simple attribute, a required single link, a required extension."""

from typing import Annotated

from scim2_models import URN
from scim2_models import ComplexAttribute
from scim2_models import Extension
from scim2_models import Mutability
from scim2_models import Required
from scim2_models import Resource
from scim2_models import Returned

from scim2_server.mapping import Link
from scim2_server.mapping import Many

from .backend import FakeMapping
from .backend import Owned
from .backend import Reference
from .backend import Table


class Owner(ComplexAttribute):
    value: str | None = None
    display: str | None = None
    aliases: list[str] | None = None


class Secret(ComplexAttribute):
    value: Annotated[str | None, Mutability.write_only] = None


class Vault(ComplexAttribute):
    code: Annotated[str | None, Returned.never] = None


class Keeper(Extension):
    __schema__ = URN("urn:example:2.0:Keeper")
    keeper: Annotated[Owner | None, Required.true] = None


class Pet(Resource):
    __schema__ = URN("urn:example:2.0:Pet")
    name: str | None = None
    tags: list[str] | None = None
    owner: Annotated[Owner | None, Required.true] = None
    secrets: list[Secret] | None = None
    vault: Vault | None = None
    safe: Annotated[Vault | None, Mutability.immutable] = None


pets = Table("pets")
pets.field("name")
pets.field("code")
pets.field("parent")
pets.field("owner", null=False)
pets.field("keeper", null=False, default=0)

tags = Table("tags")
tags.field("pet", null=False)
tags.field("value")
tags.field("label", null=False)

secrets = Table("secrets")
secrets.field("pet", null=False)
secrets.field("value")

PET_TABLES = (pets, tags, secrets)


def pets_mapping(model=Pet, changes=None, **options):
    attributes = {
        "id": pets.id,
        "name": pets.fields["name"],
        "tags": Many(Owned(tags, "pet"), tags.fields["value"]),
        "owner": Link(Reference(pets.fields["owner"], pets), "Pet"),
    }
    attributes |= changes or {}
    attributes = {
        path: value for path, value in attributes.items() if value is not None
    }
    return FakeMapping(model, pets, attributes, **options)
