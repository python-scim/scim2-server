import datetime
import decimal
import json
import uuid

import pytest
from scim2_models import InvalidCursorException
from scim2_models import SearchRequest
from scim2_models import User

from scim2_server.keyset import Bound
from scim2_server.keyset import Position
from scim2_server.keyset import decode_sort_value
from scim2_server.keyset import encode_sort_value
from scim2_server.keyset import keyset_page
from scim2_server.keyset import page_rows


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        3,
        1.5,
        "text",
        datetime.datetime(2026, 10, 9, 12, tzinfo=datetime.UTC),
        datetime.date(2026, 10, 9),
        decimal.Decimal("1.50"),
        uuid.UUID(int=1),
    ],
)
def test_a_sort_value_survives_its_position(value):
    """A sort value is read back from its position in its own type, after going through JSON."""
    decoded = decode_sort_value(json.loads(json.dumps(encode_sort_value(value))))

    assert decoded == value
    assert type(decoded) is type(value)


def test_a_sort_value_of_an_unknown_type_cannot_be_paged():
    """A sort value JSON cannot hold is refused rather than written wrong."""
    with pytest.raises(TypeError, match="bytes"):
        encode_sort_value(b"bytes")


@pytest.mark.parametrize(
    "value",
    [
        ["a"],
        {"a": "1", "b": "2"},
        {"bytes": "x"},
        {"date": 1},
        {"decimal": "x"},
        {"datetime": "never"},
    ],
)
def test_a_malformed_sort_value_is_an_invalid_cursor(value):
    """A sort value that no position holds raises invalidCursor."""
    with pytest.raises(InvalidCursorException):
        decode_sort_value(value)


def test_no_position_is_the_first_page():
    """The first page of a search has no position."""
    assert Position.load(None, has_sort=True) is None


@pytest.mark.parametrize(
    "position, type_count",
    [
        (Position(True, None, "x"), None),
        (Position(False, datetime.date(2026, 10, 9), "x"), None),
        (Position(True, "bob", "x", 0), 2),
        (Position(False, None, "x", 1), 2),
    ],
)
def test_a_position_survives_json(position, type_count):
    """A position is read back from the value it dumps, after going through JSON."""
    value = json.loads(json.dumps(position.dump()))

    assert Position.load(value, has_sort=True, type_count=type_count) == position


def test_a_position_dumps_the_known_format():
    """A dumped position holds the direction, the sort value, the resource type and the identifier."""
    position = Position(False, datetime.date(2026, 10, 9), "x", 1)

    assert position.dump() == {
        "d": "previous",
        "k": {"date": "2026-10-09"},
        "t": 1,
        "i": "x",
    }


@pytest.mark.parametrize(
    "value",
    [
        "next",
        [1],
        {"d": "next", "i": "x"},
        {"d": "next", "k": None, "i": "x", "extra": 1},
        {"d": "next", "k": None, "t": 0, "i": "x"},
        {"d": "sideways", "k": None, "i": "x"},
        {"d": "next", "k": None, "i": 1},
        {"d": "next", "k": {"datetime": "never"}, "i": "x"},
    ],
)
def test_a_malformed_position_is_an_invalid_cursor(value):
    """A position that load did not get from dump raises invalidCursor."""
    with pytest.raises(InvalidCursorException):
        Position.load(value, has_sort=True)


def test_a_position_without_sort_holds_no_sort_value():
    """Without a sort, a position holding a sort value raises invalidCursor."""
    assert Position.load(
        {"d": "next", "k": None, "i": "x"}, has_sort=False
    ) == Position(True, None, "x")
    with pytest.raises(InvalidCursorException):
        Position.load({"d": "next", "k": "bob", "i": "x"}, has_sort=False)


@pytest.mark.parametrize(
    "value",
    [
        {"d": "next", "k": None, "i": "x"},
        {"d": "next", "k": None, "t": True, "i": "x"},
        {"d": "next", "k": None, "t": "0", "i": "x"},
        {"d": "next", "k": None, "t": -1, "i": "x"},
        {"d": "next", "k": None, "t": 2, "i": "x"},
    ],
)
def test_a_malformed_root_position_is_an_invalid_cursor(value):
    """A position ordered by resource type needs the index of one of the resource types."""
    with pytest.raises(InvalidCursorException):
        Position.load(value, has_sort=True, type_count=2)


@pytest.mark.parametrize("type_count", [1, 2])
def test_a_position_by_resource_type_survives_json(type_count):
    """A position ordered by resource type is read back with any number of resource types."""
    position = Position(True, None, "x", 0)
    value = json.loads(json.dumps(position.dump()))

    assert Position.load(value, has_sort=True, type_count=type_count) == position


def test_page_rows_without_cursor():
    """Without a cursor, the rows are the page, and no page is said to follow."""
    assert page_rows((1, 2, 3), SearchRequest(count=2), None) == ([1, 2, 3], False)


@pytest.mark.parametrize(
    "count, position, expected",
    [
        (None, None, ([1, 2, 3], False)),
        (3, None, ([1, 2, 3], False)),
        (2, None, ([1, 2], True)),
        (2, Position(True, None, "x"), ([1, 2], True)),
        (2, Position(False, None, "x"), ([2, 1], True)),
        (3, Position(False, None, "x"), ([3, 2, 1], False)),
    ],
)
def test_page_rows_with_a_cursor(count, position, expected):
    """The extra row tells that a page follows, and the rows read backward are put back in the sort order."""
    search_request = SearchRequest(cursor="", count=count)

    assert page_rows((1, 2, 3), search_request, position) == expected


FIRST = Bound("alice", "1")
LAST = Bound("carol", "3")
NEXT = {"d": "next", "k": "carol", "i": "3"}
PREVIOUS = {"d": "previous", "k": "alice", "i": "1"}


@pytest.mark.parametrize(
    "more, position, expected_next, expected_previous",
    [
        (True, None, NEXT, None),
        (False, None, None, None),
        (True, Position(True, "aaron", "0"), NEXT, PREVIOUS),
        (False, Position(True, "aaron", "0"), None, PREVIOUS),
        (True, Position(False, "dave", "4"), NEXT, PREVIOUS),
        (False, Position(False, "dave", "4"), NEXT, None),
    ],
    ids=[
        "first page with more",
        "single page",
        "forward with more",
        "last page",
        "backward with more",
        "back to the first page",
    ],
)
def test_keyset_page(more, position, expected_next, expected_previous):
    """A page links to the page after its last row and to the page before its first row, when they exist."""
    resources = [User(user_name="alice"), User(user_name="carol")]

    page = keyset_page(5, resources, FIRST, LAST, more, position)

    assert page.total == 5
    assert page.resources == resources
    assert page.next == expected_next
    assert page.previous == expected_previous


def test_keyset_page_without_rows():
    """An empty page links to no other page."""
    page = keyset_page(0, [], None, None, True, Position(True, "aaron", "0"))

    assert page.resources == []
    assert page.next is None
    assert page.previous is None


def test_keyset_page_at_the_root():
    """The positions around a page at the root hold the index of the resource type of the row."""
    page = keyset_page(
        None,
        [User(user_name="alice")],
        Bound(None, "1", 0),
        Bound(None, "1", 0),
        True,
        None,
    )

    assert page.next == {"d": "next", "k": None, "t": 0, "i": "1"}
