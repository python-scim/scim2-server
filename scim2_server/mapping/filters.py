"""SCIM filters and sorts, turned into the conditions of a backend.

The core holds the SCIM rules: the scope of a value selection, ne on a
collection, missing values, substrings, strings the policy cannot prepare,
constants. The backend gives the primitives, through :class:`Predicates`.
"""

from collections.abc import Callable
from collections.abc import Iterable
from enum import Enum
from functools import partial
from typing import Any
from typing import Protocol
from typing import TypeVar
from typing import cast

from scim2_models import AttributeBinding
from scim2_models import InvalidFilterException
from scim2_models import InvalidPathException
from scim2_models import ScimFilter
from scim2_models import ScimPolicy
from scim2_models import SearchRequest
from scim2_models.path import AttrPath
from scim2_models.path import CompareOperator
from scim2_models.path import Comparison
from scim2_models.path import FilterVisitor
from scim2_models.path import LogicalExpr
from scim2_models.path import LogicalOperator
from scim2_models.path import Not
from scim2_models.path import Present
from scim2_models.path import ValuePath
from scim2_models.path import coerce_value

from .descriptors import Collection
from .descriptors import Column
from .descriptors import EntryColumns
from .mapping import ResourceMapping

C = TypeVar("C")

SUBSTRINGS = (CompareOperator.co, CompareOperator.sw, CompareOperator.ew)


class Predicates(Protocol[C]):
    """The conditions a backend builds, on the records or on the entries of a collection.

    The core only calls compare and compare_text with a value that is not
    None, and compare_text with a string the policy can prepare. compare
    never gets a substring operator.

    Each condition is true or false, never unknown as a SQL NULL is, so that
    its negation holds when it does not. compare_text is false for a stored
    string the policy cannot prepare.
    """

    def true(self) -> C: ...

    def false(self) -> C: ...

    def any(self, conditions: Iterable[C]) -> C: ...

    def all(self, conditions: Iterable[C]) -> C: ...

    def negate(self, condition: C) -> C: ...

    def is_null(self, column: Column) -> C: ...

    def is_empty(self, column: Column) -> C:
        """Return the condition that a string column holds an empty string."""
        ...

    def compare(self, column: Column, op: CompareOperator, value: Any) -> C: ...

    def compare_text(self, column: Column, op: CompareOperator, value: str) -> C:
        """Compare a string column with the comparison key of the policy."""
        ...

    def compare_key(self, column: Column, value: Any) -> C | None:
        """Return the condition that a keyed column equals a value, or None when no record can hold it."""
        ...

    def exists(
        self,
        collection: Collection,
        build: Callable[["Predicates[C]"], C] | None = None,
    ) -> C:
        """Return the condition that an entry exists, matching what build returns from the conditions on the entries."""
        ...


def as_stored(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value

    return value


def holds(
    binding: AttributeBinding,
    op: CompareOperator,
    actual: Any,
    expected: Any,
    policy: ScimPolicy,
) -> bool:
    """Tell whether a value matches a comparison, as ScimFilter.match tells it."""
    if actual is None or expected is None:
        if op == CompareOperator.eq:
            return actual is None and expected is None

        if op == CompareOperator.ne:
            return (actual is None) != (expected is None)

        return False

    try:
        left = binding.comparable(actual, policy)
        right = binding.comparable(expected, policy)
    except ValueError:
        return op == CompareOperator.ne

    if op in SUBSTRINGS:
        if not isinstance(left, str) or not isinstance(right, str):
            return False

        if op == CompareOperator.co:
            return right in left

        if op == CompareOperator.sw:
            return left.startswith(right)

        return left.endswith(right)

    try:
        return bool(
            {
                CompareOperator.eq: lambda: left == right,
                CompareOperator.ne: lambda: left != right,
                CompareOperator.gt: lambda: left > right,
                CompareOperator.ge: lambda: left >= right,
                CompareOperator.lt: lambda: left < right,
                CompareOperator.le: lambda: left <= right,
            }[op]()
        )
    except TypeError:
        return False


def unmapped(binding: AttributeBinding) -> InvalidFilterException:
    return InvalidFilterException(detail=f"Cannot filter on '{binding.urn}'")


Holder = Collection | EntryColumns


class FilterTranslator(FilterVisitor[C]):
    """Turn a SCIM filter into a condition on the records of a mapping.

    The condition keeps the records whose resources ScimFilter.match keeps.
    scope is the collection, or the Entry, a value selection applies its
    filter to.
    """

    def __init__(
        self,
        mapping: ResourceMapping,
        scim_filter: ScimFilter[Any],
        conditions: Predicates[C],
        policy: ScimPolicy,
        scope: tuple[AttrPath, Holder] | None = None,
    ) -> None:
        self.mapping = mapping
        self.filter = scim_filter
        self.conditions = conditions
        self.policy = policy
        self.scope = scope

    def _resolve(
        self, attr_path: AttrPath, resolve: Callable[..., AttributeBinding | None]
    ) -> AttributeBinding | None:
        if self.scope is None:
            return resolve(attr_path, strict=False)

        scope_path, scope = self.scope
        if scope.simple:
            own = attr_path.attr.lower() == "value" and not attr_path.sub_attr
            return scope.binding if own else None

        return resolve(
            AttrPath(scope_path.attr, attr_path.attr, scope_path.uri), strict=False
        )

    def _lookup(
        self, binding: AttributeBinding
    ) -> list[tuple[Collection | None, Column | None]]:
        pairs = self.mapping._lookup(binding)
        if self.scope is None:
            return pairs

        _, scope = self.scope
        if isinstance(scope, EntryColumns):
            return pairs

        return [
            (collection, column) for collection, column in pairs if collection is scope
        ]

    def _targets(
        self, binding: AttributeBinding
    ) -> list[tuple[Collection | None, Column]]:
        pairs = self._lookup(binding)
        if any(column is None or column.concealed for _, column in pairs):
            raise unmapped(binding)

        return pairs  # type: ignore[return-value]

    def _in_collection(
        self, collection: Collection | None, build: Callable[[Predicates[C]], C]
    ) -> C:
        if collection is None or self.scope is not None:
            return build(self.conditions)

        return self.conditions.exists(collection, build)

    def _holder_exists(self, holder: Holder, build: Callable[[Predicates[C]], C]) -> C:
        if isinstance(holder, EntryColumns):
            return self.conditions.all(
                [entry_present(self.conditions, holder), build(self.conditions)]
            )

        return self.conditions.exists(holder, build)

    def _compare(
        self, conditions: Predicates[C], column: Column, op: CompareOperator, value: Any
    ) -> C:
        return compare(self.mapping, conditions, self.policy, column, op, value)

    def _scoped(self, node: ValuePath, holder: Holder, conditions: Predicates[C]) -> C:
        translator = FilterTranslator(
            self.mapping, self.filter, conditions, self.policy, (node.attr_path, holder)
        )
        return translator.visit(node.val_filter)

    def visit_comparison(self, node: Comparison) -> C:
        binding = self._resolve(node.attr_path, self.filter.resolve_comparison)
        if binding is None:
            return self.conditions.false()

        targets = self._targets(binding)
        value = coerce_value(binding, node.value, node.op)

        # 'emails ne "x"' holds when no email is "x", while
        # 'emails[value ne "x"]' holds when one email is not "x".
        if (
            node.op == CompareOperator.ne
            and targets[0][0] is not None
            and self.scope is None
        ):
            return self.conditions.negate(
                self.conditions.any(
                    self.conditions.exists(
                        collection,  # type: ignore[arg-type]
                        partial(
                            self._compare,
                            column=column,
                            op=CompareOperator.eq,
                            value=value,
                        ),
                    )
                    for collection, column in targets
                )
            )

        return self.conditions.any(
            self._in_collection(
                collection,
                partial(self._compare, column=column, op=node.op, value=value),
            )
            for collection, column in targets
        )

    def visit_present(self, node: Present) -> C:
        """Keep the records with a value. A complex attribute is present when one of its sub-attributes is."""
        binding = self._resolve(node.attr_path, self.filter.resolve)
        if binding is None:
            return self.conditions.false()

        pairs = self._lookup(binding)
        in_collection = pairs[0][0] is not None
        if in_collection and binding.sub_field_name is None and pairs[0][1] is None:
            return self.conditions.any(
                self.conditions.exists(collection)  # type: ignore[arg-type]
                for collection, _ in pairs
            )

        visible = [
            (collection, column)
            for collection, column in pairs
            if column is not None and not column.concealed
        ]
        if len(visible) == len(pairs):
            return self.conditions.any(
                self._in_collection(
                    collection, partial(present, self.mapping, column=column)
                )
                for collection, column in visible
            )

        sub_columns = [
            column
            for column in self.mapping._sub_columns(binding)
            if not column.concealed
        ]
        if in_collection or not sub_columns:
            raise unmapped(binding)

        return self.conditions.any(
            present(self.mapping, self.conditions, column) for column in sub_columns
        )

    def visit_not(self, node: Not) -> C:
        return self.conditions.negate(self.visit(node.expr))

    def visit_logical_expr(self, node: LogicalExpr) -> C:
        conditions = [self.visit(term) for term in node.terms]
        if node.op == LogicalOperator.and_:
            return self.conditions.all(conditions)

        return self.conditions.any(conditions)

    def visit_value_path(self, node: ValuePath) -> C:
        binding = self.filter.resolve(node.attr_path, strict=False)
        if binding is None:
            return self.conditions.false()

        holders: list[Holder] = [
            collection
            for collection, _ in self.mapping._lookup(binding)
            if collection is not None
        ]
        entry = self.mapping._entries.get((binding.model, binding.field_name))
        if entry is not None:
            holders = [entry]

        if not holders:
            raise unmapped(binding)

        return self.conditions.any(
            self._holder_exists(holder, partial(self._scoped, node, holder))
            for holder in holders
        )


def entry_present(conditions: Predicates[C], entry: EntryColumns) -> C:
    """Return the condition that the record holds the entry: one of its columns has a value."""
    return conditions.any(
        conditions.negate(conditions.is_null(column))
        for column in entry.columns.values()
    )


def present(mapping: ResourceMapping, conditions: Predicates[C], column: Column) -> C:
    """Per RFC 7644 §3.4.2.2, an empty string is no value."""
    if column.is_constant:
        entry = mapping._entry_of(column)
        if entry is None:
            return conditions.true()

        return entry_present(conditions, entry)

    has_value = conditions.negate(conditions.is_null(column))
    if not column.is_string:
        return has_value

    return conditions.all([has_value, conditions.negate(conditions.is_empty(column))])


def compare(
    mapping: ResourceMapping,
    conditions: Predicates[C],
    policy: ScimPolicy,
    column: Column,
    op: CompareOperator,
    value: Any,
) -> C:
    """Compare a column with a value.

    A constant is compared in Python, as it is the same for every record
    holding it. A missing value is equal to no value but a missing one.
    """
    value = as_stored(value)
    if column.is_constant:
        return _compare_constant(mapping, conditions, policy, column, op, value)

    if value is None:
        if op == CompareOperator.eq:
            return conditions.is_null(column)

        if op == CompareOperator.ne:
            return conditions.negate(conditions.is_null(column))

        return conditions.false()

    if column.keyed and op in (CompareOperator.eq, CompareOperator.ne):
        condition = conditions.compare_key(column, value)
        if condition is None:
            return conditions.true() if op == CompareOperator.ne else conditions.false()

        if op == CompareOperator.ne:
            return conditions.any(
                [conditions.is_null(column), conditions.negate(condition)]
            )

        return condition

    if not column.is_string:
        if op in SUBSTRINGS:
            return conditions.false()

        if op == CompareOperator.ne:
            return conditions.any(
                [
                    conditions.is_null(column),
                    conditions.negate(
                        conditions.compare(column, CompareOperator.eq, value)
                    ),
                ]
            )

        return conditions.all(
            [
                conditions.negate(conditions.is_null(column)),
                conditions.compare(column, op, value),
            ]
        )

    try:
        column.binding.comparable(value, policy)
    except ValueError:
        return conditions.true() if op == CompareOperator.ne else conditions.false()

    if op == CompareOperator.ne:
        return conditions.any(
            [
                conditions.is_null(column),
                conditions.negate(
                    conditions.compare_text(column, CompareOperator.eq, value)
                ),
            ]
        )

    return conditions.all(
        [
            conditions.negate(conditions.is_null(column)),
            conditions.compare_text(column, op, value),
        ]
    )


def holds_any(
    mapping: ResourceMapping,
    conditions: Predicates[C],
    policy: ScimPolicy,
    binding: AttributeBinding,
    values: list[Any],
) -> C:
    """Return the condition that a record holds one of some values of an attribute.

    The attribute is one of the unique attributes of the mapping, and the
    values are those of a resource, as
    :func:`~scim2_server.storage.unique_values` returns them. The values are
    compared as a filter with ``eq`` compares them, in the record, in an
    Entry, in the entries of a collection or in a Link. The condition does
    not leave out the written resource: the storage does.
    """

    def any_value(inner: Predicates[C], column: Column) -> C:
        return inner.any(
            compare(mapping, inner, policy, column, CompareOperator.eq, value)
            for value in values
        )

    holders = [
        (collection, cast(Column, column))
        for collection, column in mapping._lookup(binding)
    ]
    return conditions.any(
        any_value(conditions, column)
        if collection is None
        else conditions.exists(collection, partial(any_value, column=column))
        for collection, column in holders
    )


def _compare_constant(
    mapping: ResourceMapping,
    conditions: Predicates[C],
    policy: ScimPolicy,
    column: Column,
    op: CompareOperator,
    value: Any,
) -> C:
    """Compare a constant: the one of its holder when the holder exists, or else no value."""

    def outcome(actual: Any) -> C:
        matched = holds(column.binding, op, actual, value, policy)
        return conditions.true() if matched else conditions.false()

    entry = mapping._entry_of(column)
    if entry is None:
        return outcome(column.constant)

    exists = entry_present(conditions, entry)
    return conditions.any(
        [
            conditions.all([exists, outcome(column.constant)]),
            conditions.all([conditions.negate(exists), outcome(None)]),
        ]
    )


def where(
    mapping: ResourceMapping,
    search_request: SearchRequest[Any],
    conditions: Predicates[C],
    policy: ScimPolicy,
) -> C:
    """Return the condition keeping the records matching the filter of a search."""
    if search_request.filter is None:
        return conditions.true()

    scim_filter = ScimFilter[mapping.model](str(search_request.filter))  # type: ignore[name-defined]
    return FilterTranslator(mapping, scim_filter, conditions, policy).visit(
        scim_filter.ast
    )


def sort_target(
    mapping: ResourceMapping, search_request: SearchRequest[Any]
) -> tuple[Collection | None, Column] | None:
    """Return the column the records sort on, with its collection if any.

    Return None when the search has no sortBy, or when the model of the
    mapping does not declare its attribute. A value hidden from clients, or
    held by another resource, cannot sort. A constant of an Entry sorts as
    its value when the entry exists, and as no value otherwise.
    """
    binding = search_request.sort_binding(mapping.model)
    if binding is None:
        return None

    collection, column = mapping._lookup(binding)[0]
    if (
        column is None
        or column.concealed
        or (collection is not None and not collection.owned)
    ):
        raise InvalidPathException(
            path=str(search_request.sort_by),
            detail=f"Cannot sort on '{binding.urn}'",
        )

    return collection, column
