"""The values a mapping declares for each SCIM attribute.

The targets are opaque to the core: a field name for Django, a column or a
relationship for SQLAlchemy, a field for Peewee. Each backend subclasses these
classes, to name the parameters after its ORM and to document them.
"""

from collections.abc import Mapping
from typing import Any

from scim2_models import AttributeBinding
from scim2_models import Mutability
from scim2_models import Required
from scim2_models import Returned


class Attribute:
    """A value holding a SCIM attribute, whose writing can be turned off."""

    def __init__(self, target: Any, *, writable: bool = True) -> None:
        self.target = target
        self.writable = writable


class Many:
    """The entries of a multi-valued attribute, stored in related records.

    attributes maps each sub-attribute name to its target in the entry
    records, or is a single target for a multi-valued simple attribute.
    """

    def __init__(self, relation: Any, attributes: Mapping[str, Any] | Any) -> None:
        self.relation = relation
        self.attributes = attributes


class Entry:
    """A multi-valued complex attribute holding one entry at most, stored in the resource record.

    constants holds the sub-attributes the entry always has, such as its type.
    """

    def __init__(
        self,
        attributes: Mapping[str, Any],
        *,
        type: str | None = None,
        primary: bool | None = None,
    ) -> None:
        self.attributes = attributes
        self.constants = {
            name: value
            for name, value in (("type", type), ("primary", primary))
            if value is not None
        }


class Link:
    """A complex attribute linking to the resources of one resource type."""

    def __init__(
        self, relation: Any, resource_type: str, *, type: str | None = None
    ) -> None:
        self.relation = relation
        self.resource_type = resource_type
        self.type = type


def key(binding: AttributeBinding) -> tuple[Any, ...]:
    """Return what tells an attribute apart, whatever the case of its path."""
    return (binding.model, binding.field_name, binding.sub_field_name)


def overlap(key: tuple[Any, ...], other: tuple[Any, ...]) -> bool:
    """Tell whether two attributes are the same, or one holds the other."""
    return key == other or (key[:2] == other[:2] and None in (key[2], other[2]))


def mutabilities(binding: AttributeBinding) -> set[Mutability | None]:
    """Return the mutabilities of an attribute, on its own or through its parent."""
    return {
        binding.model.get_field_annotation(binding.field_name, Mutability),
        binding.get_annotation(Mutability),
    }


def never_returned(binding: AttributeBinding) -> bool:
    """Tell whether an attribute is never returned, on its own or through its parent."""
    return Returned.never in (
        binding.model.get_field_annotation(binding.field_name, Returned),
        binding.get_annotation(Returned),
    )


def required(binding: AttributeBinding) -> bool:
    """Tell whether an attribute is required where it is defined."""
    return Required.true in (
        binding.model.get_field_annotation(binding.field_name, Required),
        binding.get_annotation(Required),
    )
