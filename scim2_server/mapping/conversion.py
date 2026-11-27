"""Resources built from records, and the checks and plans of their writes."""

import hashlib
import hmac
import json
from collections.abc import Iterable
from collections.abc import Mapping
from inspect import isclass
from typing import Any

from pydantic import BaseModel
from scim2_models import AttributeBinding
from scim2_models import InvalidValueException
from scim2_models import Meta
from scim2_models import Mutability
from scim2_models import MutabilityException
from scim2_models import Path
from scim2_models import Resource
from scim2_models import ScimPolicy

from .descriptors import Collection
from .descriptors import Column
from .descriptors import Storage
from .filters import as_stored
from .mapping import ResourceMapping


def comparable(value: Any) -> Any:
    """Return a value in a form two resources compare in. A list of missing values is no value."""
    if isinstance(value, list):
        values = [as_stored(item) for item in value]
        return None if all(item is None for item in values) else values

    return as_stored(value)


def values(binding: AttributeBinding, resource: Resource[Any]) -> list[Any]:
    return list(Path(binding.urn).get(resource, strict=False) or [])


def entries(collection: Collection, resource: Resource[Any]) -> list[Any]:
    """Return the entries a resource holds for a collection, as a list even for a single one."""
    if collection.single:
        value = Path(collection.binding.urn).get(resource, strict=False)
        return [] if value is None else [value]

    return values(collection.binding, resource)


def version_hash(resource: Resource[Any], key: bytes) -> str:
    """Return the weak ETag of the hash of a resource.

    The hash covers the values hidden from clients, so it is keyed: a version
    tells nothing about these values.
    """
    data = json.dumps(resource.model_dump(mode="json"), sort_keys=True)
    digest = hmac.new(key, data.encode(), hashlib.sha256)
    return f'W/"{digest.hexdigest()[:32]}"'


def to_scim(
    mapping: ResourceMapping,
    record: Any,
    resource_type: str,
    model: type[Resource[Any]],
    endpoints: Mapping[str, str],
    collections: Iterable[Collection],
    version_key: bytes | None,
) -> Resource[Any]:
    """Build the resource a record stores, reading the given collections only."""
    resource = model()
    special = (mapping._id, mapping._created, mapping._last_modified)
    for column in mapping._columns.values():
        if column in special or column.in_entry:
            continue

        value = mapping._read(column, record)
        if value is not None:
            Path[model](column.binding.urn).set(resource, value)  # type: ignore[valid-type]

    for entry in mapping._entries.values():
        found = {
            column.binding.urn.rsplit(".", 1)[-1]: mapping._read(column, record)
            for column in entry.columns.values()
        }
        if any(value is not None for value in found.values()):
            found |= {name: column.constant for name, column in entry.constants.items()}
            Path[model](entry.binding.urn).set(resource, [found])  # type: ignore[valid-type]

    read: dict[str, tuple[bool, list[Any]]] = {}
    for collection in collections:
        endpoint = None
        if Path[model](f"{collection.binding.urn}.$ref").resolve() is not None:  # type: ignore[valid-type]
            endpoint = endpoints.get(collection.resource_type)  # type: ignore[arg-type]

        _, found_entries = read.setdefault(
            collection.binding.urn, (collection.single, [])
        )
        for related in mapping._related(collection, record):
            entry = _read_entry(mapping, collection, related, endpoint)
            if entry is not None:
                found_entries.append(entry)

    for urn, (single, found_entries) in read.items():
        if found_entries:
            Path[model](urn).set(  # type: ignore[valid-type]
                resource, found_entries[0] if single else found_entries
            )

    resource.id = str(mapping._read(mapping._id, record))
    resource.meta = Meta(
        resource_type=resource_type,
        created=_read_date(mapping, mapping._created, record),
        last_modified=_read_date(mapping, mapping._last_modified, record),
    )
    resource = model.model_validate(resource.model_dump())
    if mapping.version is not None:
        version = f'W/"{mapping._counter(record)}"'
    else:
        if version_key is None:
            raise ValueError(f"{mapping!r} has no counter, and needs a version key")

        version = version_hash(resource, version_key)

    resource.meta.version = version  # type: ignore[union-attr]
    return resource


def _read_date(mapping: ResourceMapping, column: Column | None, record: Any) -> Any:
    return None if column is None else mapping._read(column, record)


def _read_entry(
    mapping: ResourceMapping, collection: Collection, entry: Any, endpoint: str | None
) -> Any:
    """Return the values of an entry, or the value of an entry of a simple collection.

    The entries of a Link are records of its target, which reads them.
    """
    reader = mapping if collection.owned else collection.target
    assert reader is not None
    if collection.simple:
        return reader._read(collection.columns[None], entry)

    found = {
        column.binding.urn.rsplit(".", 1)[-1]: reader._read(column, entry)
        for column in collection.columns.values()
    }
    if collection.owned:
        return found

    found["value"] = str(found["value"])
    if endpoint is not None:
        found["$ref"] = f"{endpoint}/{found['value']}"

    if collection.link_type is not None:
        found["type"] = collection.link_type

    return found


def check_storable(
    mapping: ResourceMapping,
    resource: Resource[Any],
    stored: Resource[Any] | None,
) -> None:
    """Refuse a change of a resource that the mapping cannot store.

    Each attribute is compared with the stored resource, None for a new one.
    A value equal to the stored one changes nothing. The read-only attributes
    of the model are skipped, as RFC 7644 §3.5.1 has them ignored.
    """
    _check_entry_values(mapping, resource)
    _check_single_entries(mapping, resource)
    model = type(resource)
    for path in Path[model].iter_paths():  # type: ignore[valid-type]
        binding = path.resolve()
        if binding is None:
            continue

        target = binding.target_type
        if isclass(target) and issubclass(target, BaseModel):
            continue

        if Mutability.read_only in (
            binding.model.get_field_annotation(binding.field_name, Mutability),
            binding.get_annotation(Mutability),
        ):
            continue

        storage = mapping._storage(binding.urn)
        value = comparable(path.get(resource, strict=False))
        current = None
        if stored is not None:
            current = comparable(Path(binding.urn).get(stored, strict=False))

        if storage == Storage.written:
            _check_written(mapping, binding, value, current, stored is None)
            continue

        if storage in (
            Storage.derived,
            Storage.ref,
            Storage.link_type,
            Storage.constant,
        ) or value in (None, current):
            continue

        if storage == Storage.read_only:
            raise MutabilityException(
                attribute=binding.urn,
                mutability="readOnly",
                detail=f"'{binding.urn}' is read-only on this server",
            )

        raise InvalidValueException(
            detail=f"'{binding.urn}' is not stored by this server"
        )


def _check_written(
    mapping: ResourceMapping,
    binding: AttributeBinding,
    value: Any,
    current: Any,
    creating: bool,
) -> None:
    _check_enumerated(mapping, binding, value)
    if value is not None:
        return

    if creating and mapping._required(binding.urn):
        raise InvalidValueException(
            detail=f"'{binding.urn}' is required on this server"
        )

    if current is not None and not mapping._nullable(binding.urn):
        raise MutabilityException(
            attribute=binding.urn,
            detail=f"'{binding.urn}' cannot be removed on this server",
        )


def _check_entry_values(mapping: ResourceMapping, resource: Resource[Any]) -> None:
    for collection in mapping._all_collections():
        if not collection.owned or not collection.writable or collection.simple:
            continue

        for entry in entries(collection, resource):
            for column in collection.columns.values():
                value = getattr(entry, column.binding.target_field_name, None)
                if value is None and column.required:
                    raise InvalidValueException(
                        detail=f"'{column.binding.urn}' is required on this server"
                    )


def _check_single_entries(mapping: ResourceMapping, resource: Resource[Any]) -> None:
    """Refuse several entries for an Entry, or an entry with another type than its constant."""
    for entry in mapping._entries.values():
        found = values(entry.binding, resource)
        if len(found) > 1:
            raise InvalidValueException(
                detail=f"'{entry.binding.urn}' holds one entry at most on this server"
            )

        for item in found:
            for name, column in entry.constants.items():
                if name == "primary":
                    continue

                value = as_stored(getattr(item, column.binding.target_field_name, None))
                if value is not None and value.casefold() != column.constant.casefold():
                    raise InvalidValueException(
                        detail=f"'{column.binding.urn}' can only be "
                        f"{column.constant!r} on this server"
                    )


def _check_enumerated(
    mapping: ResourceMapping, binding: AttributeBinding, value: Any
) -> None:
    """Refuse a value that a field with choices does not store.

    The resource may be of another class than the model of the mapping, such
    as the model of the provider, so the attribute is looked up by its URN.
    """
    own = Path[mapping.model](binding.urn).resolve()  # type: ignore[name-defined]
    assert own is not None
    _, column = mapping._lookup(own)[0]
    if column is None or column.enumeration is None:
        return

    for item in value if isinstance(value, list) else [value]:
        if item is not None and mapping._as_stored(item) not in column.enumeration:
            raise InvalidValueException(
                detail=f"'{binding.urn}' cannot hold {item!r} on this server"
            )


def keeps(
    mapping: ResourceMapping, column: Column, value: Any, record: Any, creating: bool
) -> bool:
    """Tell whether a value leaves a record as it is.

    When creating, a missing value keeps the default, unless the backend
    writes a value for none. When updating, a value equal to the one read is
    kept, so that a password hash is not hashed again.
    """
    if creating:
        return value is None and not column.fills_none

    return bool(value == mapping._read(column, record))


def write_plan(
    mapping: ResourceMapping,
    resource: Resource[Any],
    record: Any,
    creating: bool,
) -> list[tuple[Column, Any]]:
    """Return the columns of the resource record to write, with their SCIM values.

    The backend converts each value into what its record stores. The dates,
    the collections and the related records are written apart.
    """
    plan: list[tuple[Column, Any]] = []
    for column in mapping._columns.values():
        if not column.writable or column.related or column.in_entry:
            continue

        value = Path(column.binding.urn).get(resource, strict=False)
        if not keeps(mapping, column, value, record, creating):
            plan.append((column, value))

    for entry in mapping._entries.values():
        found = values(entry.binding, resource)
        if not entry.writable or (creating and not found):
            continue

        item = found[0] if found else None
        for column in entry.columns.values():
            value = getattr(item, column.binding.target_field_name, None)
            if not keeps(mapping, column, value, record, creating):
                plan.append((column, value))

    return plan


def dates(
    mapping: ResourceMapping, now: Any, creating: bool
) -> list[tuple[Column, Any]]:
    """Return the date columns set to now: the modification date, and the creation date when creating."""
    columns = [mapping._last_modified, mapping._created if creating else None]
    return [(column, now) for column in columns if column is not None]


def link_entries(
    mapping: ResourceMapping, resource: Resource[Any]
) -> list[tuple[list[Collection], list[Any]]]:
    """Return the entries of each written attribute linking to other resources, with the collections of its Links.

    An entry without value, such as one with a $ref only, is refused rather than lost.
    """
    links = []
    for collections in mapping._collections.values():
        collection = collections[0]
        if collection.owned or not collection.writable:
            continue

        found = entries(collection, resource)
        if any(entry.value is None for entry in found):
            raise InvalidValueException(
                detail=f"'{collection.binding.urn}' holds an entry without value"
            )

        links.append((collections, found))

    return links


def same_type(collection: Collection, value: str, policy: ScimPolicy) -> bool:
    """Tell whether a type is the one a Link gives, compared as filters compare it."""
    binding = collection.type_column.binding  # type: ignore[union-attr]
    try:
        return bool(
            binding.comparable(value, policy)
            == binding.comparable(collection.link_type, policy)
        )
    except ValueError:
        return False


def assign_links(
    found: list[tuple[Collection, list[Any]]],
    link_values: list[Any],
    policy: ScimPolicy,
) -> dict[Collection, list[Any]]:
    """Give each entry the record it links to, in the first Link holding that record.

    found holds the records each Link holds among the values of the entries.
    An entry with a type links to a Link giving the same type.
    """
    links: dict[Collection, list[Any]] = {collection: [] for collection, _ in found}
    urn = found[0][0].binding.urn
    for entry in link_values:
        matches = [
            (collection, record)
            for collection, records in found
            for record in records
            if collection.target._holds_id(record, entry.value)  # type: ignore[union-attr]
        ]
        if not matches:
            raise InvalidValueException(detail=f"'{urn}' links to an unknown resource")

        entry_type = getattr(entry, "type", None)
        if entry_type is not None:
            matches = [
                (collection, record)
                for collection, record in matches
                if same_type(collection, entry_type, policy)
            ]

        if not matches:
            raise InvalidValueException(
                detail=f"'{urn}' links to {entry.value!r}, which is not "
                f"of type {entry_type!r}"
            )

        collection, record = matches[0]
        if record not in links[collection]:
            links[collection].append(record)

    return links
