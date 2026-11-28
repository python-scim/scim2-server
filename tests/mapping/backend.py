"""A backend of the mapping core for the tests: records in dicts, conditions in Python predicates."""

import datetime
import operator
from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Iterator
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Any

from scim2_models import AttributeBinding
from scim2_models import Mutability
from scim2_models import NotFoundException
from scim2_models import PreconditionFailedException
from scim2_models import Resource
from scim2_models import ResourceType
from scim2_models import ResponseParameters
from scim2_models import ScimPolicy
from scim2_models import ScimProvider
from scim2_models import SearchRequest
from scim2_models.path import CompareOperator

from scim2_server import mapping as core
from scim2_server.mapping import conversion
from scim2_server.mapping.declaration import mutabilities
from scim2_server.mapping.filters import as_stored
from scim2_server.mapping.filters import compare
from scim2_server.mapping.filters import entry_present
from scim2_server.mapping.filters import sort_target
from scim2_server.mapping.filters import where
from scim2_server.storage import ScimStorage
from scim2_server.storage import SearchPage
from scim2_server.storage import uniqueness_conflict

Row = dict[str, Any]
Predicate = Callable[[Row], bool]


@dataclass(eq=False)
class Field:
    table: "Table"
    name: str
    null: bool = True
    default: Any = None
    choices: list[Any] | None = None


class Table:
    """The rows of a model, by primary key. Each row holds its key in "id"."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.fields: dict[str, Field] = {}
        self.rows: dict[int, Row] = {}
        self.next_key = 1
        self.id = self.field("id", null=False)

    def __repr__(self) -> str:
        return self.name

    def field(self, name: str, **options: Any) -> Field:
        self.fields[name] = Field(self, name, **options)
        return self.fields[name]

    def new(self) -> Row:
        return {name: field.default for name, field in self.fields.items()}

    def insert(self, row: Row) -> None:
        row["id"] = self.next_key
        self.rows[self.next_key] = row
        self.next_key += 1


@dataclass(frozen=True)
class Owned:
    """The rows of target holding the key of their record in key."""

    target: Table
    key: str


@dataclass(frozen=True)
class Reference:
    """A field of the record holding the key of a row of target."""

    field: Field
    target: Table


@dataclass(frozen=True)
class References:
    """A field of the record holding the keys of rows of target."""

    field: Field
    target: Table


@dataclass(frozen=True)
class Referrers:
    """The rows whose References field holds the key of the record."""

    field: Field

    @property
    def target(self) -> Table:
        return self.field.table


@dataclass(eq=False)
class FakeColumn(core.Column):
    field: Field = dataclass_field(default=None)


def related(collection: core.Collection, row: Row) -> list[Row]:
    relation = collection.relation
    if isinstance(relation, Owned):
        found = relation.target.rows.values()
        return [entry for entry in found if entry[relation.key] == row["id"]]

    if isinstance(relation, Reference):
        key = row[relation.field.name]
        return [] if key is None else [relation.target.rows[key]]

    if isinstance(relation, References):
        keys = sorted(row[relation.field.name])
        return [
            relation.target.rows[key] for key in keys if key in relation.target.rows
        ]

    found = relation.target.rows.values()
    return [entry for entry in found if row["id"] in entry[relation.field.name]]


def _keys(
    row: Row, field: Field, column: core.Column | None, policy: ScimPolicy
) -> list[Any]:
    """Return the values of a row a unique constraint compares, as the filters compare them."""
    held = row[field.name]
    values = [
        value
        for value in (held if isinstance(held, list) else [held])
        if value is not None
    ]
    if column is None or not column.is_string:
        return values

    return [column.binding.comparable(value, policy) for value in values]


def needs_value(field: Field) -> bool:
    return not (field.null or field.default is not None or field.name == "id")


class FakeMapping(core.ResourceMapping):
    """A mapping on tables, resolved when created unless resolve is False.

    :raises ValueError: When a field does not fit its attribute.
    """

    def __init__(
        self,
        model: type[Resource[Any]],
        record: Table,
        attributes: Mapping[str, Any],
        *,
        version: Field | None = None,
        resolve: bool = True,
    ) -> None:
        super().__init__(model, record, attributes, version=version)
        if not resolve:
            return

        errors = [problem for problem in self._resolve() if problem.level == "error"]
        if errors:
            raise ValueError("\n".join(problem.message for problem in errors))

    def _is_target(self, value: Any) -> bool:
        return isinstance(value, Field)

    def _build_column(
        self,
        path: str,
        binding: AttributeBinding,
        value: Any,
        owner: core.Collection | None,
    ) -> core.mapping.Problems:
        field = value.target if isinstance(value, core.Attribute) else value
        table = self.record if owner is None else owner.relation.target
        if field.table is not table:
            yield core.MappingProblem(
                "error",
                "unknown-field",
                f"{path!r} is not mapped to a field of {table}",
            )
            return None

        is_id = owner is None and path == "id"
        column = FakeColumn(
            binding,
            field.name,
            Mutability.read_only not in mutabilities(binding)
            and self._value_writable(value)
            and not is_id,
            textual=is_id,
            keyed=is_id,
            nullable=field.null,
            needs_value=needs_value(field),
            enumeration=field.choices,
            stores=field.name if owner is None else None,
            field=field,
        )
        return column

    def _build_collection(
        self,
        path: str,
        binding: AttributeBinding,
        declaration: core.Many | core.Link,
    ) -> core.mapping.Problems:
        relation = declaration.relation
        if isinstance(declaration, core.Many):
            kinds: tuple[type, ...] = (Owned,)
        elif binding.is_multivalued:
            kinds = (References, Referrers)
        else:
            kinds = (Reference,)

        if not isinstance(relation, kinds):
            yield core.MappingProblem(
                "error", "relation-kind", f"{path!r} cannot be mapped to {relation!r}"
            )
            return None

        if isinstance(relation, Reference):
            return core.Collection(
                binding,
                relation,
                nullable=relation.field.null,
                stores=relation.field.name,
            )

        return core.Collection(binding, relation)

    def _fields_needing_value(self) -> Iterable[str]:
        return [
            field.name
            for field in self.record.fields.values()
            if needs_value(field) and field is not self.version
        ]

    def _links_to(self, collection: core.Collection, target: Any) -> bool:
        return bool(collection.relation.target is target.record)

    def _read(self, column: core.Column, record: Any) -> Any:
        return record.get(column.field.name)

    def _related(self, collection: core.Collection, record: Any) -> list[Any]:
        return related(collection, record)

    def _counter(self, record: Any) -> Any:
        return record[self.version.name]

    def _holds_id(self, record: Any, value: str) -> bool:
        return str(record["id"]) == value


_OPERATORS: dict[CompareOperator, Callable[[Any, Any], Any]] = {
    CompareOperator.eq: operator.eq,
    CompareOperator.gt: operator.gt,
    CompareOperator.ge: operator.ge,
    CompareOperator.lt: operator.lt,
    CompareOperator.le: operator.le,
    CompareOperator.co: operator.contains,
    CompareOperator.sw: str.startswith,
    CompareOperator.ew: str.endswith,
}


class FakePredicates:
    """Predicates on rows, as SQL evaluates them: a comparison with no value does not hold."""

    def __init__(self, policy: ScimPolicy) -> None:
        self.policy = policy

    def true(self) -> Predicate:
        return lambda row: True

    def false(self) -> Predicate:
        return lambda row: False

    def any(self, conditions: Iterable[Predicate]) -> Predicate:
        conditions = list(conditions)
        return lambda row: any(condition(row) for condition in conditions)

    def all(self, conditions: Iterable[Predicate]) -> Predicate:
        conditions = list(conditions)
        return lambda row: all(condition(row) for condition in conditions)

    def negate(self, condition: Predicate) -> Predicate:
        return lambda row: not condition(row)

    def is_null(self, column: core.Column) -> Predicate:
        return lambda row: row[column.field.name] is None

    def is_empty(self, column: core.Column) -> Predicate:
        return lambda row: row[column.field.name] == ""

    def compare(
        self, column: core.Column, op: CompareOperator, value: Any
    ) -> Predicate:
        name = column.field.name
        return lambda row: _OPERATORS[op](row[name], value)

    def compare_text(
        self, column: core.Column, op: CompareOperator, value: str
    ) -> Predicate:
        name = column.field.name
        binding = column.binding
        right = binding.comparable(value, self.policy)
        return lambda row: _OPERATORS[op](
            binding.comparable(row[name], self.policy), right
        )

    def compare_key(self, column: core.Column, value: Any) -> Predicate | None:
        if not value.isdecimal() or str(int(value)) != value:
            return None

        name = column.field.name
        return lambda row: row[name] == int(value)

    def exists(
        self,
        collection: core.Collection,
        build: Callable[[Any], Predicate] | None = None,
    ) -> Predicate:
        matches = self.true() if build is None else build(self)
        return lambda row: any(matches(entry) for entry in related(collection, row))


class FakeStorage(ScimStorage):
    """Store the resources in the rows of tables, through the mapping core."""

    supports_cursors = False

    def __init__(
        self,
        mappings: Mapping[str, FakeMapping],
        provider: ScimProvider,
        *,
        version_key: bytes | None = None,
    ) -> None:
        self.mappings = dict(mappings)
        self.provider = provider
        self.version_key = version_key
        self.policy = provider.policy
        self.conditions = FakePredicates(self.policy)
        self.endpoints = {
            str(resource_type.name): str(resource_type.endpoint).strip("/")
            for resource_type in provider.resource_types
        }
        links = [mapping._targets(self.mappings) for mapping in self.mappings.values()]
        for mapping, targets in zip(self.mappings.values(), links, strict=True):
            mapping._bind(targets)

    def _to_scim(
        self,
        resource_type: ResourceType,
        row: Row,
        parameters: ResponseParameters[Any] | None = None,
    ) -> Resource[Any]:
        mapping = self.mappings[str(resource_type.name)]
        return conversion.to_scim(
            mapping,
            row,
            str(resource_type.name),
            self.provider.model_for(resource_type),
            self.endpoints,
            mapping._returned_collections(parameters),
            self.version_key,
        )

    def _with_id(self, mapping: FakeMapping, resource_id: str) -> Predicate:
        return compare(
            mapping,
            self.conditions,
            self.policy,
            mapping._id,
            CompareOperator.eq,
            resource_id,
        )

    def _find(self, mapping: FakeMapping, condition: Predicate) -> list[Row]:
        return [row for row in mapping.record.rows.values() if condition(row)]

    def _load(self, resource_type: ResourceType, resource_id: str) -> Row:
        mapping = self.mappings[str(resource_type.name)]
        found = self._find(mapping, self._with_id(mapping, resource_id))
        if not found:
            raise NotFoundException(detail=f"{resource_id!r} not found")

        return found[0]

    def get(
        self,
        resource_type: ResourceType,
        resource_id: str,
        *,
        response_parameters: ResponseParameters[Any] | None = None,
    ) -> Resource[Any]:
        row = self._load(resource_type, resource_id)
        return self._to_scim(resource_type, row, response_parameters)

    def search(
        self,
        resource_types: list[ResourceType],
        search_request: SearchRequest[Any],
        *,
        position: Any = None,
    ) -> SearchPage:
        (resource_type,) = resource_types
        mapping = self.mappings[str(resource_type.name)]
        condition = where(mapping, search_request, self.conditions, self.policy)
        rows = self._sort(mapping, search_request, self._find(mapping, condition))
        start = search_request.start_index_0 or 0
        stop = None if search_request.count is None else start + search_request.count
        resources = [
            self._to_scim(resource_type, row, search_request)
            for row in rows[start:stop]
        ]
        return SearchPage(len(rows), resources)

    def _sort(
        self, mapping: FakeMapping, search_request: SearchRequest[Any], rows: list[Row]
    ) -> list[Row]:
        """Sort as SQL does: the rows without value last when ascending, first when descending."""
        target = sort_target(mapping, search_request)
        if target is None:
            return rows

        collection, column = target
        keyed = [
            (self._sort_value(mapping, collection, column, row), row) for row in rows
        ]
        present = [(value, row) for value, row in keyed if value is not None]
        missing = [row for value, row in keyed if value is None]
        descending = search_request.sort_order == SearchRequest.SortOrder.descending
        present.sort(key=operator.itemgetter(0), reverse=descending)
        ordered = [row for _, row in present]
        return [*missing, *ordered] if descending else [*ordered, *missing]

    def _sort_value(
        self,
        mapping: FakeMapping,
        collection: core.Collection | None,
        column: core.Column,
        row: Row,
    ) -> Any:
        """Return the value of the row, or of its primary entry, or else of its first one.

        A constant is the value of the entry holding it, when the entry exists.
        """
        if column.is_constant:
            holder = entry_present(self.conditions, mapping._entry_of(column))
            value = column.constant if holder(row) else None
            return self._sort_key(column, value)

        if collection is not None:
            entries = related(collection, row)
            entries.sort(key=lambda entry: entry.get("primary") is not True)

            if not entries:
                return None

            row = entries[0]

        return self._sort_key(column, row[column.field.name])

    def _sort_key(self, column: core.Column, value: Any) -> Any:
        if value is None or not column.is_string:
            return value

        return column.binding.comparable(value, self.policy)

    def create(
        self, resource_type: ResourceType, resource: Resource[Any]
    ) -> Resource[Any]:
        mapping = self.mappings[str(resource_type.name)]
        conversion.check_storable(mapping, resource, None)
        links = self._links(mapping, resource)
        row = mapping.record.new()
        with self._unique_constraints():
            self._write(mapping, resource, row, links, creating=True)

        return self._to_scim(resource_type, row)

    def update(
        self,
        resource_type: ResourceType,
        resource: Resource[Any],
        *,
        expected_version: str | None = None,
    ) -> Resource[Any]:
        mapping = self.mappings[str(resource_type.name)]
        row = self._load(resource_type, str(resource.id))
        stored = self._to_scim(resource_type, row)
        _check_version(stored, expected_version)
        conversion.check_storable(mapping, resource, stored)
        links = self._links(mapping, resource)
        with self._unique_constraints():
            if mapping.version is not None:
                row[mapping.version.name] += 1

            self._write(mapping, resource, row, links, creating=False)

        return self._to_scim(resource_type, row)

    def delete(
        self,
        resource_type: ResourceType,
        resource_id: str,
        *,
        expected_version: str | None = None,
    ) -> None:
        mapping = self.mappings[str(resource_type.name)]
        row = self._load(resource_type, resource_id)
        _check_version(self._to_scim(resource_type, row), expected_version)
        for collection in mapping._all_collections():
            if isinstance(collection.relation, Owned):
                for entry in related(collection, row):
                    del collection.relation.target.rows[entry["id"]]

        del mapping.record.rows[row["id"]]

    def _write(
        self,
        mapping: FakeMapping,
        resource: Resource[Any],
        row: Row,
        links: dict[core.Collection, list[Row]],
        creating: bool,
    ) -> None:
        now = datetime.datetime.now(datetime.UTC)
        for column, value in [
            *conversion.write_plan(mapping, resource, row, creating),
            *conversion.dates(mapping, now, creating),
        ]:
            row[column.field.name] = as_stored(value)

        if creating:
            mapping.record.insert(row)

        for collection in mapping._all_collections():
            if collection.writable:
                self._write_collection(collection, resource, row, links)

    def _write_collection(
        self,
        collection: core.Collection,
        resource: Resource[Any],
        row: Row,
        links: dict[core.Collection, list[Row]],
    ) -> None:
        relation = collection.relation
        if isinstance(relation, Reference):
            linked = links[collection]
            row[relation.field.name] = linked[0]["id"] if linked else None
            return

        if isinstance(relation, References):
            row[relation.field.name] = [linked["id"] for linked in links[collection]]
            return

        for entry in related(collection, row):
            del relation.target.rows[entry["id"]]

        for entry in conversion.entries(collection, resource):
            entry_row = relation.target.new()
            entry_row[relation.key] = row["id"]
            for name, column in collection.columns.items():
                value = entry
                if name is not None:
                    value = getattr(entry, column.binding.target_field_name)

                entry_row[column.field.name] = as_stored(value)

            relation.target.insert(entry_row)

    @contextmanager
    def _unique_constraints(self) -> Iterator[None]:
        """Run a write as a transaction, and roll it back when it breaks a unique constraint.

        As a database would, each field holding a unique attribute gets a
        constraint comparing the values as the filters do. Two mappings
        sharing a table share its constraints.
        """
        tables = {
            table
            for mapping in self.mappings.values()
            for table in (
                mapping.record,
                *(c.relation.target for c in mapping._all_collections()),
            )
        }
        snapshot = {
            table: ({key: dict(row) for key, row in table.rows.items()}, table.next_key)
            for table in tables
        }
        yield
        for binding, table, field, column in self._constraints():
            keys = [
                key
                for row in table.rows.values()
                for key in _keys(row, field, column, self.policy)
            ]
            if len(keys) == len(set(keys)):
                continue

            for table, (rows, next_key) in snapshot.items():
                table.rows, table.next_key = rows, next_key

            raise uniqueness_conflict(binding)

    def _constraints(
        self,
    ) -> Iterator[tuple[AttributeBinding, Table, Field, core.Column | None]]:
        """Yield the unique constraints, with their attribute and the column comparing the strings of each."""
        for mapping in self.mappings.values():
            for binding in mapping.unique_attributes().values():
                for collection, column in mapping._lookup(binding):
                    relation = None if collection is None else collection.relation
                    if relation is None:
                        yield binding, mapping.record, column.field, column
                    elif isinstance(relation, Owned):
                        yield binding, relation.target, column.field, column
                    elif isinstance(relation, Reference | References):
                        yield binding, mapping.record, relation.field, None
                    else:
                        raise NotImplementedError

    def _links(
        self, mapping: FakeMapping, resource: Resource[Any]
    ) -> dict[core.Collection, list[Row]]:
        links: dict[core.Collection, list[Row]] = {}
        for collections, entries in conversion.link_entries(mapping, resource):
            found = [
                (collection, self._linked_rows(collection, entries))
                for collection in collections
            ]
            links |= conversion.assign_links(found, entries, self.policy)

        return links

    def _linked_rows(
        self, collection: core.Collection, entries: list[Any]
    ) -> list[Row]:
        target: FakeMapping = collection.target
        condition = self.conditions.any(
            self._with_id(target, entry.value) for entry in entries
        )
        return self._find(target, condition)


def _check_version(stored: Resource[Any], expected_version: str | None) -> None:
    if expected_version is not None and stored.meta.version != expected_version:
        raise PreconditionFailedException
