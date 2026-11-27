import pytest
from scim2_models import InvalidFilterException
from scim2_models import ScimFilter
from scim2_models import ScimPolicy
from scim2_models import SearchRequest
from scim2_models import User
from scim2_models.path import CompareOperator

from scim2_server.mapping.filters import holds
from scim2_server.mapping.filters import where

from .backend import FakePredicates
from .policy import REFUSING
from .tables import users_mapping


def binding(path):
    return ScimFilter[User](f"{path} pr").resolve(
        ScimFilter[User](f"{path} pr").ast.attr_path
    )


@pytest.mark.parametrize(
    ("op", "actual", "expected", "result"),
    [
        (CompareOperator.eq, None, None, True),
        (CompareOperator.eq, "a", None, False),
        (CompareOperator.ne, None, "a", True),
        (CompareOperator.ne, None, None, False),
        (CompareOperator.gt, None, "a", False),
        (CompareOperator.eq, "Bob", "bob", True),
        (CompareOperator.co, "Bob", "O", True),
        (CompareOperator.sw, "Bob", "bo", True),
        (CompareOperator.ew, "Bob", "bo", False),
        (CompareOperator.ne, "Bob", "bob", False),
        (CompareOperator.gt, "Bob", "alice", True),
        (CompareOperator.ge, "Bob", "bob", True),
        (CompareOperator.lt, "Bob", "alice", False),
        (CompareOperator.le, "Bob", "bob", True),
    ],
)
def test_holds_compares_as_the_filter_evaluator(op, actual, expected, result):
    """Missing values, substrings and orders follow ScimFilter.match."""
    user_name = binding("userName")

    assert holds(user_name, op, actual, expected, ScimPolicy()) is result


def test_holds_finds_no_substring_in_a_value_that_is_not_a_string():
    """A boolean contains nothing."""
    active = binding("active")

    assert not holds(active, CompareOperator.co, True, True, ScimPolicy())


def test_holds_finds_no_order_between_values_of_different_types():
    """A boolean is neither above nor below a string."""
    active = binding("active")

    assert not holds(active, CompareOperator.gt, True, "a", ScimPolicy())


@pytest.mark.parametrize(
    ("op", "result"), [(CompareOperator.eq, False), (CompareOperator.ne, True)]
)
def test_holds_finds_a_string_the_policy_cannot_prepare_different(op, result):
    """Only ne holds against a string the policy refuses."""
    policy = REFUSING

    assert holds(binding("userName"), op, "bob", "bob!", policy) is result


def found(storage, resource_type, scim_filter):
    """Return the names of the resources a filter finds, checked against the filter evaluator."""
    everything = storage.search([resource_type], SearchRequest()).resources
    expected = sorted(
        r.id for r in everything if ScimFilter[type(r)](scim_filter).match(r)
    )
    page = storage.search([resource_type], SearchRequest(filter=scim_filter))
    assert sorted(r.id for r in page.resources) == expected
    return [r.id for r in page.resources]


@pytest.mark.parametrize(
    "scim_filter",
    [
        'id eq "1"',
        'id ne "1"',
        'id eq "abc"',
        'id ne "abc"',
        'id eq "01"',
        'foo eq "x"',
        "foo pr",
        'foo[bar eq "x"]',
        'emails[foo eq "x"]',
        'groups.value eq "1"',
        "groups pr",
        'groups.type eq "direct"',
        'groups.type eq "DIRECT"',
        'groups.type ne "direct"',
        "groups.type pr",
        'groups[type eq "direct" and value eq "1"]',
        'groups.display eq "admins"',
    ],
)
def test_a_filter_on_the_users_finds_what_the_evaluator_finds(
    users_storage, scim_filter
):
    """Identifiers, unknown attributes and the groups of the users."""
    storage, user_type, _ = users_storage

    found(storage, user_type, scim_filter)


@pytest.mark.parametrize(
    "scim_filter",
    [
        'members.value eq "1"',
        'members.type eq "Group"',
        'members[type eq "User" and value eq "2"]',
        'members[type eq "Group"]',
        "members.type pr",
        'members.display eq "admins"',
        'members ne "1"',
    ],
)
def test_a_filter_on_the_groups_finds_what_the_evaluator_finds(
    users_storage, scim_filter
):
    """Members of two resource types, told apart by their type."""
    storage, _, group_type = users_storage

    found(storage, group_type, scim_filter)


@pytest.mark.parametrize(
    "scim_filter",
    [
        'password eq "x"',
        "password pr",
        'emails.display eq "x"',
        "emails.display pr",
        "addresses pr",
        'addresses[type eq "work"]',
    ],
)
def test_a_filter_on_a_value_the_mapping_does_not_store_is_refused(
    users_storage, scim_filter
):
    """A hidden or unmapped value cannot be filtered on."""
    storage, user_type, _ = users_storage

    with pytest.raises(InvalidFilterException):
        storage.search([user_type], SearchRequest(filter=scim_filter))


@pytest.mark.parametrize(
    ("scim_filter", "expected"),
    [('userName eq "alice!"', []), ('userName ne "alice!"', ["1", "2"])],
)
def test_a_string_the_policy_cannot_prepare_equals_no_value(scim_filter, expected):
    """No user name is equal to it, and every one differs from it."""
    policy = REFUSING
    mapping = users_mapping()
    condition = where(
        mapping, SearchRequest(filter=scim_filter), FakePredicates(policy), policy
    )
    rows = [{"id": 1, "user_name": "alice"}, {"id": 2, "user_name": "bob"}]

    assert [str(row["id"]) for row in rows if condition(row)] == expected


@pytest.mark.parametrize(
    "scim_filter",
    [
        'tags eq "big"',
        'tags ne "big"',
        "tags pr",
        'tags[value sw "b"]',
        'tags[display eq "big"]',
        'owner.value eq "1"',
        "owner pr",
    ],
)
def test_a_filter_on_the_pets_finds_what_the_evaluator_finds(pets_storage, scim_filter):
    """Tags are a list of strings, and the owner a single link."""
    storage, pet_type = pets_storage

    found(storage, pet_type, scim_filter)
