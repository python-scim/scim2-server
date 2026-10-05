import sys

from scim2_models import BulkOperation
from scim2_models import Context
from scim2_models import Group
from scim2_models import GroupMember
from scim2_models import User

from scim2_server.bulk import BulkPlan
from scim2_server.bulk import replace_bulk_ids


def test_a_value_without_reference_is_kept_as_it_is():
    """Nothing is copied when there is no bulkId reference to replace."""
    group = Group(display_name="g", members=[GroupMember(value="1")])
    assert replace_bulk_ids(group, lambda bulk_id: "x") is group


def test_references_are_replaced_in_models_lists_and_dicts():
    """A reference is replaced wherever the value holds it."""
    group = Group(members=[GroupMember(value="bulkId:u")])
    replaced = replace_bulk_ids(
        {"group": group, "ids": ["bulkId:u", 1]}, lambda bulk_id: f"id-{bulk_id}"
    )
    assert replaced["group"].members[0].value == "id-u"
    assert replaced["ids"] == ["id-u", 1]
    assert group.members[0].value == "bulkId:u"


def test_the_fields_the_client_set_stay_the_same():
    """A PUT keeps the write-only values the client left out, so a replaced reference must not set more fields."""
    user = User.model_validate(
        {"userName": "bjensen", "nickName": "bulkId:u"},
        scim_ctx=Context.RESOURCE_REPLACEMENT_REQUEST,
    )
    replaced = replace_bulk_ids(user, lambda bulk_id: "id")
    assert replaced.nick_name == "id"
    assert replaced.model_fields_set == user.model_fields_set


def creation(bulk_id, reference=None):
    nick_name = f"bulkId:{reference}" if reference else None
    return BulkOperation[User](
        method=BulkOperation.Method.post,
        path="/Users",
        bulk_id=bulk_id,
        data=User(user_name=bulk_id, nick_name=nick_name),
    )


def test_a_creation_is_planned_before_the_operations_referencing_it():
    """An operation referencing a later creation runs after it, the others keep their order."""
    plan = BulkPlan([creation("a", "c"), creation("b"), creation("c")], None)

    assert [step.index for step in plan] == [2, 0, 1]


def test_a_long_chain_of_references_is_planned():
    """A chain of references longer than the recursion limit is planned in order."""
    count = sys.getrecursionlimit() + 100
    operations = [creation(f"u{i}", f"u{i + 1}") for i in range(count - 1)]
    operations.append(creation(f"u{count - 1}"))

    plan = BulkPlan(operations, None)

    assert [step.index for step in plan] == list(reversed(range(count)))


def test_the_plan_stops_at_the_errors_the_client_accepts():
    """No step is yielded once failOnErrors is reached."""
    plan = BulkPlan([creation("a"), creation("b"), creation("c")], 1)
    steps = iter(plan)

    plan.record(next(steps), {"status": 400, "bulk_id": "a"}, None)

    assert list(steps) == []
    assert [result["bulk_id"] for result in plan.outcomes()] == ["a"]
