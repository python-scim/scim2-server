"""The base of the mappings of the backends: declaration, resolution and SCIM rules."""

from collections.abc import Generator
from collections.abc import Iterable
from collections.abc import Iterator
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import replace
from enum import Enum
from inspect import isclass
from typing import Any

from pydantic import BaseModel
from scim2_models import AttributeBinding
from scim2_models import Extension
from scim2_models import Mutability
from scim2_models import Path
from scim2_models import Required
from scim2_models import Resource
from scim2_models import ResponseParameters
from scim2_models import Returned
from scim2_models import Schema
from scim2_models import Uniqueness

from .declaration import Attribute
from .declaration import Entry
from .declaration import Link
from .declaration import Many
from .declaration import key
from .declaration import mutabilities
from .declaration import never_returned
from .declaration import overlap
from .declaration import required
from .descriptors import Collection
from .descriptors import Column
from .descriptors import EntryColumns
from .descriptors import Storage
from .descriptors import column_storage


@dataclass(frozen=True)
class MappingProblem:
    """A problem of a mapping, which the backend reports in its own way.

    level is "error" or "warning". code tells the rule apart, so that a
    backend can give it its own identifier.
    """

    level: str
    code: str
    message: str


Problems = Generator[MappingProblem, None, Any]


class ResourceMapping:
    """How the resources of a SCIM model are stored in the records of a backend.

    The paths are checked when the mapping is created, which raises
    :class:`ValueError` for a path out of the model. The backend resolves the
    targets once its models are ready, and gets the problems of the mapping
    as :class:`MappingProblem` objects.
    """

    REQUIRED = ("id",)

    configuration_error: type[Exception] = ValueError
    """The exception refusing mappings that do not fit together, such as a Link to a missing mapping."""

    def __init__(
        self,
        model: type[Resource[Any]],
        record: Any,
        attributes: Mapping[str, Any],
        *,
        version: Any = None,
    ) -> None:
        self.model = model
        self.record = record
        self.version = version
        self._declared: dict[str, tuple[AttributeBinding, Any]] = {}
        self._columns: dict[tuple[Any, ...], Column] = {}
        self._collections: dict[tuple[Any, ...], list[Collection]] = {}
        self._entries: dict[tuple[Any, ...], EntryColumns] = {}
        self._problems: list[MappingProblem] | None = None
        self._id: Column
        self._created: Column | None = None
        self._last_modified: Column | None = None

        declared: dict[tuple[Any, ...], str] = {}
        for path, value in attributes.items():
            binding = self._resolve_path(path)
            binding_key = key(binding)
            for other, other_path in declared.items():
                if overlap(binding_key, other):
                    raise ValueError(
                        f"{other_path!r} and {path!r} map the same attribute"
                    )

            declared[binding_key] = path
            self._declare(path, binding, value)

        for path in self.REQUIRED:
            if key(self._resolve_path(path)) not in declared:
                raise ValueError(f"The mapping of {model.__name__} needs {path!r}")

    def __repr__(self) -> str:
        return f"<{type(self).__name__} of {self.model.__name__}>"

    def _is_target(self, value: Any) -> bool:
        """Tell whether a value is a target the backend maps a simple attribute to."""
        raise NotImplementedError

    def _value_writable(self, value: Any) -> bool:
        """Tell whether the backend writes a value declared for a simple attribute."""
        return not isinstance(value, Attribute) or value.writable

    def _check_declared(
        self,
        path: str,
        binding: AttributeBinding,
        value: Any,
        holder: Many | Entry | None,
    ) -> None:
        """Refuse a value the backend cannot map, by raising ValueError.

        holder is the Many or the Entry holding the value, or None for a value
        of the resource record.
        """

    def _build_column(
        self,
        path: str,
        binding: AttributeBinding,
        value: Any,
        owner: Collection | None,
    ) -> Problems:
        """Build the column of a value, in the resource record or in the entries of owner."""
        raise NotImplementedError

    def _build_collection(
        self, path: str, binding: AttributeBinding, declaration: Many | Link
    ) -> Problems:
        """Build the collection of a Many or of a Link, without its columns."""
        raise NotImplementedError

    def _check_record(self) -> Problems:
        """Yield the problems of the backend once every column is built."""
        yield from ()

    def _fields_needing_value(self) -> Iterable[str]:
        """Return the record fields a new record needs a value for."""
        return ()

    def _links_to(self, collection: Collection, target: "ResourceMapping") -> bool:
        """Tell whether the records of a Link are the records of a target mapping."""
        raise NotImplementedError

    def _read(self, column: Column, record: Any) -> Any:
        """Return the SCIM value a record holds for a column."""
        raise NotImplementedError

    def _related(self, collection: Collection, record: Any) -> list[Any]:
        """Return the entry records of a collection of a record, in their order."""
        raise NotImplementedError

    def _counter(self, record: Any) -> Any:
        """Return the write counter of a record."""
        raise NotImplementedError

    def _holds_id(self, record: Any, value: str) -> bool:
        """Tell whether a record of this mapping has an identifier."""
        raise NotImplementedError

    def _as_stored(self, value: Any) -> Any:
        """Return a SCIM value in the form the backend stores it, such as an Enum as its value."""
        return value.value if isinstance(value, Enum) else value

    def _find(self, path: str) -> AttributeBinding | None:
        return Path[self.model](path).resolve()  # type: ignore[name-defined]

    def _resolve_path(self, path: str) -> AttributeBinding:
        binding = self._find(path)
        if binding is None:
            raise ValueError(f"{path!r} is not an attribute of {self.model.__name__}")

        return binding

    def _declare(self, path: str, binding: AttributeBinding, value: Any) -> None:
        target = binding.target_type
        is_complex = isclass(target) and issubclass(target, BaseModel)

        if isinstance(value, Many):
            self._check_many(path, binding, value, is_complex)
            self._declared[path] = (binding, value)
            return

        if isinstance(value, Entry):
            self._check_entry(path, binding, value, is_complex)
            self._declared[path] = (binding, value)
            return

        if isinstance(value, Link):
            value = [value]

        if isinstance(value, list) and all(isinstance(item, Link) for item in value):
            if not is_complex or binding.sub_field_name is not None:
                raise ValueError(
                    f"{path!r} is not a complex attribute of {self.model.__name__}"
                )

            if not binding.is_multivalued and len(value) > 1:
                raise ValueError(
                    f"{path!r} holds one value, so it is mapped to one Link"
                )

            self._check_links(path, value)
            self._declared[path] = (binding, value)
            return

        if binding.is_multivalued or is_complex:
            raise ValueError(
                f"{path!r} holds several values or sub-attributes: map it with Many "
                "or Link, or map its sub-attributes"
            )

        self._check_single(path, binding)
        self._check_value(path, value)
        self._check_declared(path, binding, value, None)
        self._check_read_and_written(path, binding, value)
        self._declared[path] = (binding, value)

    def _check_value(self, path: str, value: Any) -> None:
        target = value.target if isinstance(value, Attribute) else value
        if not self._is_target(target):
            raise ValueError(f"{path!r} cannot be mapped to {value!r}")

    def _check_read_and_written(
        self, path: str, binding: AttributeBinding, value: Any
    ) -> None:
        read = Mutability.write_only not in mutabilities(
            binding
        ) and not never_returned(binding)
        written = Mutability.read_only not in mutabilities(
            binding
        ) and self._value_writable(value)
        if not (read or written):
            raise ValueError(f"Clients could neither read nor write {path!r}")

    def _check_single(self, path: str, binding: AttributeBinding) -> None:
        if binding.sub_field_name is not None and binding.target_is_multivalued:
            raise ValueError(
                f"{path!r} holds several values in a complex attribute, "
                "which the storage cannot map"
            )

    def _check_many(
        self, path: str, binding: AttributeBinding, many: Many, is_complex: bool
    ) -> None:
        simple = not isinstance(many.attributes, Mapping)
        if (
            binding.sub_field_name is not None
            or not binding.is_multivalued
            or is_complex == simple
        ):
            kind = "simple" if simple else "complex"
            raise ValueError(
                f"{path!r} is not a multi-valued {kind} attribute of "
                f"{self.model.__name__}"
            )

        if simple:
            self._check_value(path, many.attributes)
            self._check_declared(path, binding, many.attributes, many)
            return

        entries: dict[tuple[Any, ...], str] = {}
        for sub_path, sub_value in many.attributes.items():
            sub_binding = self._resolve_path(f"{path}.{sub_path}")
            if key(sub_binding) in entries:
                raise ValueError(
                    f"{entries[key(sub_binding)]!r} and "
                    f"{f'{path}.{sub_path}'!r} map the same attribute"
                )

            entries[key(sub_binding)] = f"{path}.{sub_path}"
            self._check_single(f"{path}.{sub_path}", sub_binding)
            self._check_value(f"{path}.{sub_path}", sub_value)
            self._check_declared(f"{path}.{sub_path}", sub_binding, sub_value, many)

    def _check_entry(
        self, path: str, binding: AttributeBinding, entry: Entry, is_complex: bool
    ) -> None:
        if (
            binding.sub_field_name is not None
            or not binding.is_multivalued
            or not is_complex
        ):
            raise ValueError(
                f"{path!r} is not a multi-valued complex attribute of "
                f"{self.model.__name__}"
            )

        if not entry.attributes:
            raise ValueError(f"The Entry of {path!r} maps no sub-attribute")

        mapped = set()
        for sub_path, sub_value in entry.attributes.items():
            sub_binding = self._resolve_path(f"{path}.{sub_path}")
            self._check_single(f"{path}.{sub_path}", sub_binding)
            self._check_value(f"{path}.{sub_path}", sub_value)
            self._check_declared(f"{path}.{sub_path}", sub_binding, sub_value, entry)
            mapped.add(key(sub_binding))

        for name in entry.constants:
            given = self._find(f"{path}.{name}")
            if given is None or key(given) in mapped:
                raise ValueError(
                    f"The Entry of {path!r} gives {name!r}, which is not a "
                    "sub-attribute it leaves unmapped"
                )

    def _check_links(self, path: str, links: list[Link]) -> None:
        resource_types = [link.resource_type for link in links]
        if not links or len(set(resource_types)) != len(resource_types):
            raise ValueError(
                f"{path!r} needs one Link per resource type, and at least one"
            )

        typed = {link.type is not None for link in links}
        if len(typed) > 1:
            raise ValueError(f"Either every Link of {path!r} has a type, or none")

        if typed == {True} and self._find(f"{path}.type") is None:
            raise ValueError(f"{path!r} has no type sub-attribute")

    def _resolve(self) -> list[MappingProblem]:
        """Build the descriptors of the declared values, once, and return the problems of the mapping."""
        if self._problems is None:
            problems = list(self._build())
            if not any(problem.level == "error" for problem in problems):
                problems.extend(self._check_unwritten())

            self._problems = problems

        return self._problems

    def _build(self) -> Problems:
        for path, (binding, value) in self._declared.items():
            head = (binding.model, binding.field_name)
            if isinstance(value, Many):
                collection = yield from self._many(path, binding, value)
                if collection is not None:
                    self._collections[head] = [collection]
            elif isinstance(value, Entry):
                entry = yield from self._entry(path, binding, value)
                if entry is not None:
                    self._entries[head] = entry
                    for column in entry.columns.values():
                        self._columns[key(column.binding)] = column
            elif isinstance(value, list):
                collections = yield from self._links(path, binding, value)
                if collections is not None:
                    self._collections[head] = collections
            else:
                column = yield from self._build_column(path, binding, value, None)
                if column is not None:
                    self._columns[key(binding)] = column

        if not all(
            key(self._resolve_path(path)) in self._columns for path in self.REQUIRED
        ):
            return

        self._id = self._column("id")
        self._created = self._columns.get(key(self._resolve_path("meta.created")))
        self._last_modified = self._columns.get(
            key(self._resolve_path("meta.lastModified"))
        )
        yield from self._check_record()

    def _many(self, path: str, binding: AttributeBinding, many: Many) -> Problems:
        collection = yield from self._build_collection(path, binding, many)
        if collection is None:
            return None

        if not isinstance(many.attributes, Mapping):
            column = yield from self._build_column(
                path, binding, many.attributes, collection
            )
            if column is None:
                return None

            collection.columns[None] = column
        else:
            for sub_path, value in many.attributes.items():
                sub_binding = self._resolve_path(f"{path}.{sub_path}")
                column = yield from self._build_column(
                    f"{path}.{sub_path}", sub_binding, value, collection
                )
                if column is None:
                    return None

                collection.columns[sub_binding.sub_field_name] = column

        columns = collection.columns.values()
        if any(column.concealed for column in columns):
            yield MappingProblem(
                "error",
                "hidden-in-entries",
                f"The entries of {path!r} are replaced on each write, "
                "so they cannot hold values hidden from clients",
            )
            return None

        if collection.writable and not all(column.writable for column in columns):
            yield MappingProblem(
                "error",
                "read-only-in-entries",
                f"The entries of {path!r} are replaced on each write, "
                "so they cannot hold a read-only value next to written ones",
            )
            return None

        return collection

    def _entry(self, path: str, binding: AttributeBinding, entry: Entry) -> Problems:
        columns: dict[str, Column] = {}
        for sub_path, value in entry.attributes.items():
            sub_binding = self._resolve_path(f"{path}.{sub_path}")
            column = yield from self._build_column(
                f"{path}.{sub_path}", sub_binding, value, None
            )
            if column is None:
                return None

            column.in_entry = True
            columns[sub_binding.sub_field_name] = column  # type: ignore[index]

        values = columns.values()
        if any(column.concealed or not column.nullable for column in values):
            yield MappingProblem(
                "error",
                "entry-fields",
                f"The entry of {path!r} exists when one of its fields has a "
                "value, and a write removing it empties them, so they cannot "
                "be hidden from clients nor without null",
            )
            return None

        writable = [column.writable for column in values]
        if any(writable) and not all(writable):
            yield MappingProblem(
                "error",
                "entry-fields",
                f"A write removing the entry of {path!r} empties its fields, so "
                "they cannot hold a read-only value next to written ones",
            )
            return None

        constants = {
            name: Column(
                self._resolve_path(f"{path}.{name}"), name, False, constant=constant
            )
            for name, constant in entry.constants.items()
        }
        return EntryColumns(binding, columns, constants)

    def _links(
        self, path: str, binding: AttributeBinding, links: list[Link]
    ) -> Problems:
        collections: list[Collection] = []
        for link in links:
            collection = yield from self._build_collection(path, binding, link)
            if collection is None:
                return None

            if (
                collection.single
                and not collection.nullable
                and not self._always_held(binding)
            ):
                yield MappingProblem(
                    "error",
                    "single-link-null",
                    f"{path!r} can be removed, or left out with its extension, "
                    "so its relation needs to hold null",
                )
                return None

            collection.resource_type = link.resource_type
            if link.type is not None:
                collection.link_type = link.type
                collection.type_column = Column(
                    self._resolve_path(f"{path}.type"),
                    "type",
                    False,
                    constant=link.type,
                )

            collections.append(collection)

        return collections

    def _always_held(self, binding: AttributeBinding) -> bool:
        """Tell whether every resource holds an attribute: it is required, and so is its extension."""
        if not required(binding):
            return False

        if not issubclass(binding.model, Extension):
            return True

        annotation = self.model.get_field_annotation(binding.model.__name__, Required)
        return bool(annotation == Required.true)

    def _check_unwritten(self) -> Iterator[MappingProblem]:
        dates = {self._created, self._last_modified}
        written = {
            column.stores
            for column in self._columns.values()
            if column.stores is not None and (column.writable or column in dates)
        }
        written.update(
            collection.stores
            for collection in self._all_collections()
            if collection.stores is not None and collection.writable
        )
        for name in self._fields_needing_value():
            if name in written:
                continue

            yield MappingProblem(
                "warning",
                "unwritten",
                f"The field {name!r} needs a value, but the storage never writes "
                "it, so creating a resource fails: map it, or give it a default",
            )

    def _targets(
        self, mappings: Mapping[str, "ResourceMapping"]
    ) -> list[tuple[Collection, "ResourceMapping"]]:
        """Return each Link with the mapping of the resources it links to."""
        return [
            (collection, self._target(collection, mappings))
            for collection in self._all_collections()
            if not collection.owned
        ]

    def _target(
        self, collection: Collection, mappings: Mapping[str, "ResourceMapping"]
    ) -> "ResourceMapping":
        target = mappings.get(collection.resource_type)  # type: ignore[arg-type]
        if target is None:
            raise self.configuration_error(
                f"No mapping for the resource type {collection.resource_type!r}"
            )

        if collection.target is not None and collection.target is not target:
            raise self.configuration_error(
                f"{collection.binding.urn!r} already links to another mapping "
                f"of {collection.resource_type!r}"
            )

        if not self._links_to(collection, target):
            raise self.configuration_error(
                f"{collection.binding.urn!r} links to other records than those "
                f"of the mapping of {collection.resource_type!r}"
            )

        return target

    def _bind(self, links: list[tuple[Collection, "ResourceMapping"]]) -> None:
        """Bind each Link to its target, whose id and display become the columns of the entries."""
        for collection, target in links:
            collection.target = target
            collection.columns = {}
            head = collection.binding.urn
            for binding, column in (
                (self._resolve_path(f"{head}.value"), target._id),
                (
                    self._find(f"{head}.display") or self._find(f"{head}.displayName"),
                    target._display(),
                ),
            ):
                if binding is None or column is None:
                    continue

                collection.columns[binding.sub_field_name] = replace(
                    column, binding=binding, writable=False
                )

    def _display(self) -> Column | None:
        for path in ("displayName", "userName"):
            binding = self._find(path)
            if binding is None:
                continue

            column = self._columns.get(key(binding))
            if column is not None and not column.concealed:
                return column

        return None

    def schemas(self) -> list[Schema]:
        """Return the schemas of the resources, restricted to what the mapping stores."""
        models: list[Any] = [self.model, *self.model.get_extension_models().values()]
        return [self._restrict(model.to_schema()) for model in models]

    def _restrict(self, schema: Schema) -> Schema:
        attributes = []
        for attribute in schema.attributes or []:
            restricted = self._restrict_attribute(
                f"{schema.id}:{attribute.name}", attribute
            )
            if restricted is not None:
                attributes.append(restricted)

        return schema.model_copy(update={"attributes": attributes})

    def _restrict_attribute(self, urn: str, attribute: Any) -> Any:
        if attribute.sub_attributes:
            restricted = self._restrict_complex(urn, attribute)
        else:
            restricted = self._restrict_simple(urn, attribute)

        if restricted is None or not (
            restricted.returned == Returned.never
            or restricted.mutability == Mutability.write_only
        ):
            return restricted

        return restricted.model_copy(update={"required": Required.false})

    def _restrict_simple(self, urn: str, attribute: Any) -> Any:
        storage = self._storage(urn)
        if storage is None:
            return None

        if storage == Storage.link_type:
            return attribute

        if storage == Storage.written:
            if attribute.multi_valued or not self._needs_value(urn):
                return attribute

            return attribute.model_copy(update={"required": Required.true})

        if storage == Storage.constant:
            if not attribute.canonical_values:
                return attribute

            constant = self._constant_value(urn)
            return attribute.model_copy(update={"canonical_values": [constant]})

        if storage == Storage.ref:
            linked = self._linked_resource_types(urn)
            return attribute.model_copy(update={"reference_types": linked})

        return attribute.model_copy(update={"mutability": Mutability.read_only})

    def _restrict_complex(self, urn: str, attribute: Any) -> Any:
        sub_attributes = [
            restricted
            for sub_attribute in attribute.sub_attributes
            if (
                restricted := self._restrict_attribute(
                    f"{urn}.{sub_attribute.name}", sub_attribute
                )
            )
            is not None
        ]
        if not sub_attributes:
            return None

        written = any(
            sub_attribute.mutability != Mutability.read_only
            for sub_attribute in sub_attributes
        )
        update: dict[str, Any] = {
            "sub_attributes": sub_attributes,
            "mutability": attribute.mutability if written else Mutability.read_only,
        }
        if all(
            sub_attribute.returned == Returned.never for sub_attribute in sub_attributes
        ):
            update["returned"] = Returned.never
            if attribute.mutability == Mutability.read_write:
                update["mutability"] = Mutability.write_only

        if not attribute.multi_valued and any(
            self._needs_value(f"{urn}.{sub_attribute.name}")
            for sub_attribute in sub_attributes
        ):
            update["required"] = Required.true

        return attribute.model_copy(update=update)

    def _storage(self, urn: str) -> Storage | None:
        """Return how the mapping stores the attribute of a URN, or None when it does not."""
        urn_key = urn.lower()
        for column in self._columns.values():
            if column.binding.urn.lower() == urn_key:
                return column_storage(column)

        for collection in self._all_collections():
            if collection.owned:
                for column in collection.columns.values():
                    if column.binding.urn.lower() == urn_key:
                        return column_storage(column)

                continue

            head = collection.binding.urn.lower()
            if urn_key == f"{head}.value":
                return Storage.written

            if urn_key in (f"{head}.display", f"{head}.displayname"):
                return Storage.derived

            if urn_key == f"{head}.$ref":
                return Storage.ref

            if urn_key == f"{head}.type" and collection.link_type is not None:
                return Storage.link_type

        for entry in self._entries.values():
            for column in entry.constants.values():
                if column.binding.urn.lower() != urn_key:
                    continue

                return Storage.constant if entry.writable else Storage.read_only

        return None

    def _constant_value(self, urn: str) -> Any:
        urn_key = urn.lower()
        return next(
            column.constant
            for entry in self._entries.values()
            for column in entry.constants.values()
            if column.binding.urn.lower() == urn_key
        )

    def _linked_resource_types(self, urn: str) -> list[str]:
        head = urn.lower().removesuffix(".$ref")
        return [
            collection.resource_type
            for collection in self._all_collections()
            if collection.binding.urn.lower() == head
            and collection.resource_type is not None
        ]

    def _all_collections(self) -> Iterator[Collection]:
        for collections in self._collections.values():
            yield from collections

    def _column(self, path: str) -> Column:
        return self._columns[key(self._resolve_path(path))]

    def _lookup(
        self, binding: AttributeBinding
    ) -> list[tuple[Collection | None, Column | None]]:
        """Return each collection holding an attribute, if any, with the column of the attribute in it."""
        collections = self._collections.get((binding.model, binding.field_name))
        if collections is None:
            return [(None, self._columns.get(key(binding)) or self._constant(binding))]

        return [
            (collection, collection.column(binding.sub_field_name))
            for collection in collections
        ]

    def _constant(self, binding: AttributeBinding) -> Column | None:
        entry = self._entries.get((binding.model, binding.field_name))
        if entry is None:
            return None

        return entry.constants.get(binding.sub_field_name)  # type: ignore[arg-type]

    def _entry_of(self, column: Column) -> EntryColumns | None:
        """Return the Entry holding a constant column, if any."""
        for entry in self._entries.values():
            if column in entry.constants.values():
                return entry

        return None

    def _nullable(self, urn: str) -> bool:
        urn_key = urn.lower()
        return all(
            column.nullable
            for column in self._columns.values()
            if column.binding.urn.lower() == urn_key
        )

    def _required(self, urn: str) -> bool:
        return any(self._required_columns(urn))

    def _needs_value(self, urn: str) -> bool:
        """Tell whether clients must give a value for the attribute of a URN."""
        return any(
            column.writable and not column.concealed
            for column in self._required_columns(urn, entries=True)
        )

    def _required_columns(self, urn: str, *, entries: bool = False) -> Iterator[Column]:
        urn_key = urn.lower()
        columns: Iterable[Column] = self._columns.values()
        if entries:
            columns = [
                *columns,
                *(
                    column
                    for collection in self._all_collections()
                    if collection.owned
                    for column in collection.columns.values()
                ),
            ]

        for column in columns:
            if column.required and column.binding.urn.lower() == urn_key:
                yield column

    def _sub_columns(self, binding: AttributeBinding) -> list[Column]:
        return [
            column
            for (model, field_name, sub_field_name), column in self._columns.items()
            if model is binding.model
            and field_name == binding.field_name
            and sub_field_name is not None
        ]

    def _string_columns(self) -> Iterator[Column]:
        for column in self._columns.values():
            if column.is_string:
                yield column

        for collection in self._all_collections():
            for column in collection.columns.values():
                if column.is_string:
                    yield column

    def _unique_columns(self) -> Iterator[Column]:
        """Yield the written columns clients read, whose values no two resources share."""
        for column in self._columns.values():
            if column.concealed or not column.writable or column.in_entry:
                continue

            if column.binding.get_annotation(Uniqueness) in (
                Uniqueness.server,
                Uniqueness.global_,
            ):
                yield column

    def _returned_collections(
        self, parameters: ResponseParameters[Any] | None = None
    ) -> list[Collection]:
        """Return the collections a response keeps, all of them without parameters or without counter."""
        collections = list(self._all_collections())
        if parameters is None or self.version is None:
            return collections

        paths = Path[self.model].iter_paths(  # type: ignore[name-defined]
            include_subattributes=False,
            attributes=parameters.attributes,
            excluded_attributes=parameters.excluded_attributes,
        )
        bindings = (path.resolve() for path in paths)
        kept = {binding.urn for binding in bindings if binding is not None}
        return [
            collection for collection in collections if collection.binding.urn in kept
        ]
