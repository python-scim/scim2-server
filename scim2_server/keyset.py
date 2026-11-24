"""Helpers for a storage that pages with cursors by keyset.

A keyset storage orders the rows by the sort value, then by identifier. A
position holds the sort value and the identifier of the row a page starts
after, or ends before. These helpers read and write such positions, and build
the pages around them. A storage may use its own positions instead.
"""

import datetime
import decimal
import uuid
from collections.abc import Callable
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from scim2_models import InvalidCursorException
from scim2_models import Resource
from scim2_models import SearchRequest

from scim2_server.storage import SearchPage

_TAGGED: dict[str, tuple[type, Callable[[Any], str], Callable[[str], Any]]] = {
    "datetime": (
        datetime.datetime,
        datetime.datetime.isoformat,
        datetime.datetime.fromisoformat,
    ),
    "date": (datetime.date, datetime.date.isoformat, datetime.date.fromisoformat),
    "decimal": (decimal.Decimal, str, decimal.Decimal),
    "uuid": (uuid.UUID, str, uuid.UUID),
}
"""The sort values JSON has no type for, by tag. A datetime is a date too, so it comes first."""


def encode_sort_value(value: Any) -> Any:
    """Return a sort value as a value JSON can hold.

    :data:`None`, booleans, numbers and strings are returned as they are.
    A :class:`~datetime.datetime`, a :class:`~datetime.date`, a
    :class:`~decimal.Decimal` or a :class:`~uuid.UUID` is returned as an
    object with a tag, such as ``{"date": "2026-10-09"}``, so that
    :func:`decode_sort_value` gives it back in its own type.

    :raises TypeError: if the value has another type.
    """
    if value is None or isinstance(value, bool | int | float | str):
        return value

    for tag, (python_type, dump, _) in _TAGGED.items():
        if isinstance(value, python_type):
            return {tag: dump(value)}

    raise TypeError(f"Cannot page on a sort value of type {type(value).__name__}")


def decode_sort_value(value: Any) -> Any:
    """Return the sort value that :func:`encode_sort_value` gave.

    :raises ~scim2_models.InvalidCursorException: if no sort value gives this value.
    """
    if value is None or isinstance(value, bool | int | float | str):
        return value

    if not isinstance(value, dict) or len(value) != 1:
        raise InvalidCursorException

    ((tag, text),) = value.items()
    if tag not in _TAGGED or not isinstance(text, str):
        raise InvalidCursorException

    try:
        return _TAGGED[tag][2](text)
    except (ValueError, decimal.InvalidOperation):
        raise InvalidCursorException from None


@dataclass(frozen=True)
class Bound:
    """The first or the last row of a page, as :func:`keyset_page` needs it."""

    key: Any
    """The sort value of the row, or :data:`None` when it has none, or when the search has no sort."""

    id: str
    """The identifier of the resource of the row."""

    type_index: int | None = None
    """The index of the resource type of the row, in a search at the root that orders the rows by resource type."""


@dataclass(frozen=True)
class Position:
    """The row a cursor page starts after, or ends before.

    The rows with the same sort value are ordered by identifier. In a search
    at the root, a storage may order them by the index of their resource type
    first, so that two resources of different types can share an identifier.
    """

    forward: bool
    """Whether the page starts after the row, rather than ending before it."""

    key: Any
    """The sort value of the row, or :data:`None` when it has none, or when the search has no sort."""

    id: str
    """The identifier of the resource of the row."""

    type_index: int | None = None
    """The index of the resource type of the row, in a search at the root that orders the rows by resource type."""

    @classmethod
    def load(
        cls, value: Any, *, has_sort: bool, type_count: int | None = None
    ) -> "Position | None":
        """Read a position that :meth:`dump` wrote, or return :data:`None` for the first page.

        ``has_sort`` tells whether the search has a sort. Without one, the
        position holds no sort value.

        ``type_count`` is the number of resource types that the storage
        orders the rows by. A storage that does not order the rows by
        resource type leaves it out. The position holds the index of a
        resource type when ``type_count`` is given, and holds none
        otherwise. :meth:`dump` writes this index when
        :attr:`type_index` is not :data:`None`.

        :raises ~scim2_models.InvalidCursorException: if the value is not such
            a position.
        """
        if value is None:
            return None

        by_type = type_count is not None
        names = {"d", "k", "t", "i"} if by_type else {"d", "k", "i"}
        if not isinstance(value, dict) or set(value) != names:
            raise InvalidCursorException

        if value["d"] not in ("next", "previous") or not isinstance(value["i"], str):
            raise InvalidCursorException

        if not has_sort and value["k"] is not None:
            raise InvalidCursorException

        type_index = value.get("t")
        if by_type and (
            type(type_index) is not int or type_index not in range(type_count or 0)
        ):
            raise InvalidCursorException

        key = decode_sort_value(value["k"])
        return cls(value["d"] == "next", key, value["i"], type_index)

    def dump(self) -> dict[str, Any]:
        """Return the position as a value JSON can hold.

        :raises TypeError: if :func:`encode_sort_value` cannot hold the sort value.
        """
        position: dict[str, Any] = {
            "d": "next" if self.forward else "previous",
            "k": encode_sort_value(self.key),
        }
        if self.type_index is not None:
            position["t"] = self.type_index

        position["i"] = self.id
        return position


def page_rows(
    rows: Sequence[Any], search_request: SearchRequest[Any], position: Position | None
) -> tuple[list[Any], bool]:
    """Return the rows of a page in the sort order, and whether more rows follow in the reading direction.

    For a cursor page, the storage reads ``count + 1`` rows, in the reverse
    order for a page that ends before the position. Without a cursor, the
    rows are returned as they are.
    """
    if search_request.cursor is None:
        return list(rows), False

    count = search_request.count
    more = count is not None and len(rows) > count
    page = list(rows if count is None else rows[:count])
    if position is not None and not position.forward:
        page.reverse()

    return page, more


def keyset_page(
    total: int | None,
    resources: Sequence[Resource[Any]],
    first: Bound | None,
    last: Bound | None,
    more: bool,
    position: Position | None,
) -> SearchPage:
    """Return a cursor page, with the positions of the pages around it.

    ``first`` and ``last`` are the first and the last rows of the page, or
    :data:`None` when the page is empty. ``more`` tells whether more rows
    follow in the reading direction, as :func:`page_rows` gives it. In the
    other direction, the page the client comes from is assumed to still
    exist.
    """
    page = SearchPage(total, list(resources))
    if first is None or last is None:
        return page

    forward = position is None or position.forward
    if more or not forward:
        page.next = Position(True, last.key, last.id, last.type_index).dump()

    if position is not None and (more or forward):
        page.previous = Position(False, first.key, first.id, first.type_index).dump()

    return page
